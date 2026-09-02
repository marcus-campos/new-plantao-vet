"""PDF do prontuário: o documento que sai da clínica em papel.

O botão "Baixar PDF" chamava `window.print()`: prometia arquivo e entregava o
diálogo do navegador, sem timbre, sem paginação e refém do que a tela tinha
carregado. Aqui o documento é montado no servidor a partir dos MESMOS dados do
prontuário em JSON (`_assemble` em `app/api/routes/records.py`): papel e tela
não podem divergir num documento regulado.

Duas regras que este arquivo não quebra:

1. **Nada é inventado.** O que não está no prontuário não aparece no PDF. Não
   existe "número do documento" gerado aqui: a tela imprimia `id[:8]` sob o
   rótulo "Internação nº", um identificador legal inventado. O que sai é o id
   interno, dito com todas as letras.
2. **O texto sai no locale da clínica.** Nenhum rótulo é decidido aqui: todos
   passam pelo catálogo (`app/i18n/catalog.py`), inclusive o formato de data.
   O `_PENDING` abaixo é só a rede enquanto as chaves não entram nos JSONs.
"""

import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.compliance import get_profile
from app.i18n.catalog import SOURCE_LOCALE, translate
from app.models.clinic import Clinic
from app.schemas.record import RecordAuthor, RecordOut

# ---------------------------------------------------------------------------
# Catálogo pendente
#
# `translate` levanta KeyError de propósito: chave faltando é falha de teste,
# não prosa silenciosa em produção. Só que os catálogos (`app/i18n/*.json`) são
# arquivo compartilhado: enquanto as chaves abaixo não forem mescladas, um
# KeyError aqui transformaria a exportação de um documento regulado em 500. O
# catálogo SEMPRE ganha; isto é a rede embaixo dele, e some quando as chaves
# entrarem.
# ---------------------------------------------------------------------------
_PENDING: dict[str, dict[str, str]] = {
    "record.pdf.document_title": {
        "pt-BR": "Prontuário de internação",
        "en": "Hospitalization record",
    },
    "record.pdf.internal_id": {
        "pt-BR": "Identificação interna: {id}",
        "en": "Internal identifier: {id}",
    },
    "record.pdf.patient": {"pt-BR": "Paciente", "en": "Patient"},
    "record.pdf.attending": {"pt-BR": "Veterinário responsável", "en": "Attending veterinarian"},
    "record.pdf.period": {"pt-BR": "Período", "en": "Period"},
    "record.pdf.ongoing": {"pt-BR": "em andamento", "en": "ongoing"},
    "record.pdf.notes": {"pt-BR": "Evoluções diárias", "en": "Daily progress notes"},
    "record.pdf.signed_by": {"pt-BR": "Assinado: {name}", "en": "Signed: {name}"},
    "record.pdf.amendment": {"pt-BR": "adendo", "en": "amendment"},
    "record.pdf.note.subjective": {"pt-BR": "Relato", "en": "History"},
    "record.pdf.note.findings": {"pt-BR": "Exame", "en": "Exam"},
    "record.pdf.note.assessment": {"pt-BR": "Avaliação", "en": "Assessment"},
    "record.pdf.note.plan": {"pt-BR": "Conduta", "en": "Plan"},
    "record.pdf.executions": {
        "pt-BR": "Medicações administradas",
        "en": "Medications administered",
    },
    "record.pdf.occurrences": {"pt-BR": "Ocorrências", "en": "Occurrences"},
    "record.pdf.prescriptions": {"pt-BR": "Prescrições", "en": "Prescriptions"},
    "record.pdf.charges": {"pt-BR": "Lançamentos da internação", "en": "Charges for this stay"},
    "record.pdf.charges_total": {"pt-BR": "Total", "en": "Total"},
    "record.pdf.col.time": {"pt-BR": "Hora", "en": "Time"},
    "record.pdf.col.item": {"pt-BR": "Medicação", "en": "Medication"},
    "record.pdf.col.by": {"pt-BR": "Executado por", "en": "Executed by"},
    "record.pdf.col.quantity": {"pt-BR": "Qtd.", "en": "Qty."},
    "record.pdf.col.amount": {"pt-BR": "Valor", "en": "Amount"},
    "record.pdf.empty_section": {
        "pt-BR": "Sem registros nesta seção.",
        "en": "No entries in this section.",
    },
    "record.pdf.generated_at": {
        "pt-BR": "Documento gerado em {when} · registro sem rasuras, correções constam como adendo",
        "en": "Document generated on {when} · kept without erasures, corrections appear as addenda",
    },
    "record.pdf.page": {"pt-BR": "Página {page} de {total}", "en": "Page {page} of {total}"},
    "record.pdf.status.active": {"pt-BR": "internado", "en": "admitted"},
    "record.pdf.status.discharged": {"pt-BR": "alta", "en": "discharged"},
    "record.pdf.status.died": {"pt-BR": "óbito", "en": "died"},
    "record.pdf.status.left_ama": {
        "pt-BR": "retirada a pedido",
        "en": "left against medical advice",
    },
    "record.pdf.state.partial": {"pt-BR": "parcial", "en": "partial"},
    "record.pdf.state.not_done": {"pt-BR": "não realizada", "en": "not done"},
    # O motivo de uma dose não sair é vocabulário fechado da execução, e o
    # catálogo do servidor não o tinha: o prontuário imprimia "refused".
    "task.reason.refused": {"pt-BR": "Paciente recusou", "en": "Patient refused"},
    "task.reason.fasting": {"pt-BR": "Jejum", "en": "Fasting"},
    "task.reason.unavailable": {"pt-BR": "Item indisponível", "en": "Item unavailable"},
    "task.reason.vet_order": {"pt-BR": "Ordem do veterinário", "en": "Vet order"},
    "task.reason.other": {"pt-BR": "Outro motivo", "en": "Other reason"},
    # O formato da data é do IDIOMA, não do código: 30/08/2026 no Brasil e ISO
    # onde a ordem dia/mês seria adivinhação. `strftime` com nome de mês
    # dependeria do locale do processo (que num contêiner é "C"), então só
    # números entram aqui.
    "record.pdf.format.datetime": {"pt-BR": "%d/%m/%Y · %H:%M", "en": "%Y-%m-%d · %H:%M"},
    "record.pdf.format.date": {"pt-BR": "%d/%m/%Y", "en": "%Y-%m-%d"},
}

#: Idiomas que escrevem 1.234,56. Fora daqui, 1,234.56.
_COMMA_DECIMAL = ("pt", "es", "it", "de", "fr", "nl", "id", "tr", "ru")

# Paleta do documento desenhado (design/telas/Prontuario.dc.html).
INK = colors.HexColor("#17251F")
INK_2 = colors.HexColor("#4A5A52")
INK_3 = colors.HexColor("#7C8B83")
RULE = colors.HexColor("#DFE7E2")
HAIRLINE = colors.HexColor("#EDF2EF")

MARGIN = 48
CONTENT_WIDTH = A4[0] - 2 * MARGIN

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
_SMALL = ParagraphStyle("small", parent=_BASE, fontSize=8.5, leading=11.5, textColor=INK_2)
_MUTED = ParagraphStyle("muted", parent=_SMALL, textColor=INK_3)
_RIGHT = ParagraphStyle("right", parent=_SMALL, alignment=TA_RIGHT)
_LABEL = ParagraphStyle(
    "label", parent=_BASE, fontName="Helvetica-Bold", fontSize=7, leading=9.5, textColor=INK_3
)
_LABEL_RIGHT = ParagraphStyle("labelRight", parent=_LABEL, alignment=TA_RIGHT)
_CLINIC = ParagraphStyle("clinic", parent=_BASE, fontName="Helvetica-Bold", fontSize=15, leading=18)
_STRONG = ParagraphStyle("strong", parent=_BASE, fontName="Helvetica-Bold", fontSize=10, leading=13)
_SECTION = ParagraphStyle(
    "section", parent=_BASE, fontName="Helvetica-Bold", fontSize=8.5, leading=11
)


def _t(key: str, locale: str, default: str | None = None, **params: Any) -> str:
    """Rótulo do catálogo, com a rede do `_PENDING` embaixo."""
    try:
        return translate(key, locale, **params)
    except KeyError:
        pending = _PENDING.get(key)
        if pending is None:
            # Vocabulário aberto (motivo de não execução, por exemplo): o
            # prontuário mostra o que foi GRAVADO, nunca uma chave crua.
            return default if default is not None else key
        template = pending.get(locale) or pending[SOURCE_LOCALE]
        return template.format(**params)


def _safe(value: Any) -> str:
    """Texto pronto para o `Paragraph` do reportlab.

    Duas armadilhas, as duas fatais para a exportação: uma evolução com "&" ou
    "<" quebra o mini-XML do reportlab, e um caractere fora do WinAnsi (um
    emoji colado de um celular) estoura na hora de escrever a fonte. Nenhuma
    das duas pode transformar o prontuário do tutor em erro 500. O texto
    original segue intacto no JSON e no banco.
    """
    text = "" if value is None else str(value)
    text = text.encode("cp1252", errors="replace").decode("cp1252")
    return escape(text)


def _zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        # Fuso inválido no cadastro não pode impedir a entrega do prontuário.
        return ZoneInfo("UTC")


@dataclass(frozen=True)
class Letterhead:
    """A clínica que timbra o documento, congelada fora do ORM.

    A montagem do PDF roda numa thread (reportlab é CPU síncrona) e um objeto
    do SQLAlchemy atravessando essa fronteira faria I/O de banco fora do event
    loop no primeiro atributo que precisasse recarregar. Aqui só viajam
    valores.
    """

    name: str
    address: str | None
    phone: str | None
    tax_id: str | None
    locale: str
    currency: str
    timezone: str
    compliance_profile: str

    @classmethod
    def of(cls, clinic: Clinic) -> "Letterhead":
        return cls(
            name=clinic.name,
            address=clinic.address,
            phone=clinic.phone,
            tax_id=clinic.tax_id,
            locale=clinic.locale or SOURCE_LOCALE,
            currency=clinic.currency or "BRL",
            timezone=clinic.timezone or "UTC",
            compliance_profile=clinic.compliance_profile,
        )


class _Formats:
    """Relógio e régua da clínica, resolvidos uma vez por documento."""

    def __init__(self, clinic: Letterhead) -> None:
        self.locale = clinic.locale or SOURCE_LOCALE
        self.currency = clinic.currency or "BRL"
        self.zone = _zone(clinic.timezone)
        self.datetime_pattern = _t("record.pdf.format.datetime", self.locale)
        self.date_pattern = _t("record.pdf.format.date", self.locale)
        self.comma_decimal = self.locale.lower().startswith(_COMMA_DECIMAL)

    def stamp(self, value: datetime | None) -> str:
        # Tudo é gravado em UTC e lido no fuso da CLÍNICA: a dose das 10h não
        # pode virar 13h no papel entregue ao tutor.
        if value is None:
            return "—"
        return value.astimezone(self.zone).strftime(self.datetime_pattern)

    def day(self, value: datetime | None) -> str:
        if value is None:
            return "—"
        return value.astimezone(self.zone).strftime(self.date_pattern)

    def decimal(self, value: Decimal | float, places: int = 2) -> str:
        text = f"{Decimal(value):,.{places}f}"
        if self.comma_decimal:
            text = text.translate(str.maketrans({",": ".", ".": ","}))
        return text

    def money(self, minor: int | None) -> str:
        return f"{self.currency} {self.decimal(Decimal(minor or 0) / 100)}"


def _signature(author: RecordAuthor | None) -> str | None:
    """Nome + registro do conselho: o perfil `br` exige os dois em cada ato."""
    if author is None or not author.name:
        return None
    license_text = " ".join(
        part for part in (author.license_authority, author.license_number) if part
    )
    return f"{author.name} · {license_text}" if license_text else author.name


def _section(title: str) -> list[Any]:
    return [
        Spacer(1, 12),
        Paragraph(_safe(title.upper()), _SECTION),
        HRFlowable(width="100%", thickness=0.7, color=RULE, spaceBefore=3, spaceAfter=5),
    ]


def _empty(fmt: _Formats) -> Paragraph:
    return Paragraph(_safe(_t("record.pdf.empty_section", fmt.locale)), _MUTED)


def _letterhead(record: RecordOut, clinic: Letterhead, fmt: _Formats) -> list[Any]:
    """Timbre: nome, endereço, cidade, telefone e CNPJ, com a régua pesada.

    Os três últimos vinham de campos que não existiam no modelo (migração
    0014): o documento entregue ao tutor saía sem endereço, telefone nem CNPJ,
    e a tela lia tudo isso através de casts para dicionário. Campo em branco
    simplesmente não ocupa linha: timbre não inventa dado.
    """
    stamped = (clinic.address, clinic.phone, clinic.tax_id)
    identity = " · ".join(part.strip() for part in stamped if part and part.strip())
    left = [Paragraph(_safe(record.clinic_name), _CLINIC)]
    if identity:
        left.append(Paragraph(_safe(identity), _SMALL))
    right = [
        Paragraph(_safe(_t("record.pdf.document_title", fmt.locale).upper()), _LABEL_RIGHT),
        Paragraph(
            _safe(_t("record.pdf.internal_id", fmt.locale, id=record.hospitalization.id)), _RIGHT
        ),
    ]
    table = Table([[left, right]], colWidths=[CONTENT_WIDTH * 0.6, CONTENT_WIDTH * 0.4])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return [table, HRFlowable(width="100%", thickness=2, color=INK, spaceAfter=12)]


def _identity(record: RecordOut, fmt: _Formats, responsible_label: str) -> Table:
    """Bloco de três colunas: paciente / responsável técnico / período."""
    patient = record.patient
    descriptors = [patient.species if patient else None, patient.breed if patient else None]
    if patient and patient.weight_kg is not None:
        descriptors.append(f"{fmt.decimal(patient.weight_kg, 1)} kg")
    patient_line = " · ".join(
        part for part in ([patient.name if patient else None] + descriptors) if part
    )

    hospitalization = record.hospitalization
    period = "{} — {}".format(
        fmt.day(hospitalization.admitted_at),
        fmt.day(hospitalization.ended_at)
        if hospitalization.ended_at
        else _t("record.pdf.ongoing", fmt.locale),
    )

    columns: list[list[Any]] = []
    for label, value, note in (
        (
            _t("record.pdf.patient", fmt.locale),
            patient_line or "—",
            f"{responsible_label}: {record.owner_name}" if record.owner_name else None,
        ),
        (
            _t("record.pdf.attending", fmt.locale),
            _signature(record.vet) or "—",
            None,
        ),
        (
            _t("record.pdf.period", fmt.locale),
            period,
            _t(
                f"record.pdf.status.{hospitalization.status}",
                fmt.locale,
                default=hospitalization.status,
            ),
        ),
    ):
        cell = [
            Paragraph(_safe(label.upper()), _LABEL),
            Paragraph(_safe(value), _STRONG),
        ]
        if note:
            cell.append(Paragraph(_safe(note), _SMALL))
        columns.append(cell)

    table = Table([columns], colWidths=[CONTENT_WIDTH / 3] * 3)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (-1, 0), (-1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _notes(record: RecordOut, fmt: _Formats) -> list[Any]:
    story = _section(_t("record.pdf.notes", fmt.locale))
    if not record.progress_notes:
        story.append(_empty(fmt))
        return story
    for note in record.progress_notes:
        signature = _signature(
            RecordAuthor(
                name=note.author_name,
                license_number=note.author_license,
                license_authority=note.author_license_authority,
            )
        )
        signed = _t("record.pdf.signed_by", fmt.locale, name=signature or note.author_name)
        if note.amends_progress_note_id:
            signed = f"{signed} · {_t('record.pdf.amendment', fmt.locale)}"
        head = Table(
            [
                [
                    Paragraph(_safe(fmt.stamp(note.signed_at)), _STRONG),
                    Paragraph(_safe(signed), _RIGHT),
                ]
            ],
            colWidths=[CONTENT_WIDTH * 0.35, CONTENT_WIDTH * 0.65],
        )
        head.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        body = [
            Paragraph(
                f"<b>{_safe(_t(f'record.pdf.note.{field}', fmt.locale))}:</b> {_safe(text)}", _BASE
            )
            for field, text in (
                ("subjective", note.subjective),
                ("findings", note.findings),
                ("assessment", note.assessment),
                ("plan", note.plan),
            )
            if text and text.strip()
        ]
        # A assinatura não pode ficar órfã no rodapé de uma página com o texto
        # dela na seguinte: quem lê precisa ver ato e autor juntos.
        story.append(KeepTogether([head, *(body or [_empty(fmt)]), Spacer(1, 6)]))
    return story


def _grid_style(rows: int) -> TableStyle:
    return TableStyle(
        [
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, rows - 2), 0.5, HAIRLINE),
        ]
    )


def _executions(record: RecordOut, fmt: _Formats) -> list[Any]:
    """Tabela de execuções: a HORA REAL e QUEM administrou, item a item."""
    story = _section(_t("record.pdf.executions", fmt.locale))
    if not record.tasks:
        story.append(_empty(fmt))
        return story
    header = [
        Paragraph(_safe(_t(f"record.pdf.col.{key}", fmt.locale).upper()), _LABEL)
        for key in ("time", "item", "by")
    ]
    rows: list[list[Any]] = [header]
    for task in record.tasks:
        title = task.title
        if task.status != "done":
            state = _t(f"record.pdf.state.{task.status}", fmt.locale, default=task.status)
            title = f"{title} · {state}"
        rows.append(
            [
                Paragraph(_safe(fmt.stamp(task.executed_at)), _SMALL),
                Paragraph(_safe(title), _BASE),
                Paragraph(_safe(_signature(task.author) or "—"), _SMALL),
            ]
        )
    table = Table(rows, colWidths=[86, CONTENT_WIDTH - 86 - 175, 175], repeatRows=1)
    table.setStyle(_grid_style(len(rows)))
    story.append(table)
    return story


def _occurrences(record: RecordOut, fmt: _Formats) -> list[Any]:
    """O que NÃO saiu como prescrito, e por quê.

    Omitir a dose recusada deixaria o prontuário contando só a metade boa da
    história; o que não foi feito é parte do registro, não falha de registro.
    """
    story = _section(_t("record.pdf.occurrences", fmt.locale))
    deviations = [task for task in (record.tasks or []) if task.status != "done"]
    if not deviations:
        story.append(_empty(fmt))
        return story
    for task in deviations:
        parts = [task.title, _t(f"record.pdf.state.{task.status}", fmt.locale, default=task.status)]
        if task.outcome_reason:
            parts.append(
                _t(f"task.reason.{task.outcome_reason}", fmt.locale, default=task.outcome_reason)
            )
        signature = _signature(task.author)
        if signature:
            parts.append(signature)
        story.append(
            Paragraph(
                f"<b>{_safe(fmt.stamp(task.executed_at))}</b> — {_safe(' · '.join(parts))}", _BASE
            )
        )
    return story


def _prescriptions(record: RecordOut, fmt: _Formats) -> list[Any]:
    story = _section(_t("record.pdf.prescriptions", fmt.locale))
    if not record.prescriptions:
        story.append(_empty(fmt))
        return story
    rows: list[list[Any]] = [
        [
            Paragraph(_safe(_t("record.pdf.col.time", fmt.locale).upper()), _LABEL),
            Paragraph(_safe(_t("record.pdf.col.item", fmt.locale).upper()), _LABEL),
            Paragraph("", _LABEL),
        ]
    ]
    for prescription in record.prescriptions:
        rows.append(
            [
                Paragraph(_safe(fmt.stamp(prescription.starts_at)), _SMALL),
                Paragraph(_safe(prescription.name), _BASE),
                Paragraph(_safe(prescription.category), _SMALL),
            ]
        )
    table = Table(rows, colWidths=[86, CONTENT_WIDTH - 86 - 175, 175], repeatRows=1)
    table.setStyle(_grid_style(len(rows)))
    story.append(table)
    return story


def _charges(record: RecordOut, fmt: _Formats) -> list[Any]:
    story = _section(_t("record.pdf.charges", fmt.locale))
    if not record.charges:
        story.append(_empty(fmt))
        return story
    rows: list[list[Any]] = [
        [
            Paragraph(_safe(_t("record.pdf.col.quantity", fmt.locale).upper()), _LABEL),
            Paragraph(_safe(_t("record.pdf.col.item", fmt.locale).upper()), _LABEL),
            Paragraph(_safe(_t("record.pdf.col.amount", fmt.locale).upper()), _LABEL),
        ]
    ]
    total = 0
    for charge in record.charges:
        amount = charge.get("total_minor") or 0
        total += int(amount)
        rows.append(
            [
                Paragraph(_safe(charge.get("quantity") or "—"), _SMALL),
                Paragraph(_safe(charge.get("description") or "—"), _BASE),
                Paragraph(_safe(fmt.money(int(amount))), _SMALL),
            ]
        )
    rows.append(
        [
            Paragraph("", _SMALL),
            Paragraph(_safe(_t("record.pdf.charges_total", fmt.locale)), _STRONG),
            Paragraph(_safe(fmt.money(total)), _STRONG),
        ]
    )
    table = Table(rows, colWidths=[60, CONTENT_WIDTH - 60 - 120, 120], repeatRows=1)
    table.setStyle(_grid_style(len(rows)))
    story.append(table)
    return story


class _PaginatedCanvas(Canvas):
    """A numeração "Página N de M" só existe depois da última página.

    O total de páginas é desconhecido enquanto o documento é montado, então as
    páginas são guardadas e o rodapé é desenhado no fim, quando M é fato. Sem
    isso o prontuário sairia sem saber onde termina, e uma folha perdida de um
    documento regulado não teria como aparecer.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pages: list[dict[str, Any]] = []
        self.note = ""
        self.page_label = "{page}/{total}"

    def showPage(self) -> None:  # noqa: N802 (assinatura do reportlab)
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total: int) -> None:
        width = A4[0]
        self.setStrokeColor(RULE)
        self.setLineWidth(0.5)
        self.line(MARGIN, 46, width - MARGIN, 46)
        self.setFont("Helvetica", 7.5)
        self.setFillColor(INK_3)
        self.drawString(MARGIN, 34, self.note)
        self.drawRightString(
            width - MARGIN,
            34,
            self.page_label.format(page=self._pageNumber, total=total),
        )


def render_record_pdf(record: RecordOut, *, clinic: Letterhead) -> bytes:
    """O prontuário em PDF, no locale e no fuso da clínica.

    Recebe o MESMO `RecordOut` que a rota JSON devolve: seção não pedida chega
    como `None` e não vira título vazio no papel.
    """
    fmt = _Formats(clinic)
    try:
        responsible_label = _t(
            get_profile(clinic.compliance_profile).responsible_label_key, fmt.locale
        )
    except KeyError:
        # Perfil desconhecido no cadastro não pode impedir a exportação.
        responsible_label = _t("responsible.owner", fmt.locale, default="")

    story: list[Any] = [
        *_letterhead(record, clinic, fmt),
        _identity(record, fmt, responsible_label),
    ]
    if record.progress_notes is not None:
        story += _notes(record, fmt)
    if record.tasks is not None:
        story += _executions(record, fmt)
        story += _occurrences(record, fmt)
    if record.prescriptions is not None:
        story += _prescriptions(record, fmt)
    if record.charges is not None:
        story += _charges(record, fmt)

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=64,
        title=_t("record.pdf.document_title", fmt.locale),
        author=record.clinic_name,
        subject=str(record.hospitalization.id),
    )
    note = _t("record.pdf.generated_at", fmt.locale, when=fmt.stamp(record.generated_at))
    page_label = _t("record.pdf.page", fmt.locale, page="{page}", total="{total}")

    def make_canvas(*args: Any, **kwargs: Any) -> _PaginatedCanvas:
        made = _PaginatedCanvas(*args, **kwargs)
        made.note = note
        made.page_label = page_label
        return made

    document.build(story, canvasmaker=make_canvas)
    return buffer.getvalue()


def record_filename(hospitalization_id: uuid.UUID) -> str:
    """Nome do arquivo em ASCII e sem prosa: identificador, não rótulo."""
    return f"record-{hospitalization_id}.pdf"
