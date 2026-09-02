import re
import unicodedata
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import get_profile
from app.core.errors import AppError
from app.models.clinic import Clinic
from app.models.hospitalization import Hospitalization
from app.models.owner import Owner
from app.models.patient import Patient
from app.models.patient_identifier import PatientIdentifier


@dataclass
class PatientHit:
    patient: Patient
    owner: Owner
    identifiers: list[PatientIdentifier]
    active_hospitalization_id: uuid.UUID | None


def normalize(value: str) -> str:
    """Documento e microchip são comparados só pelos dígitos.

    Quem digita CPF põe ponto e traço; quem lê microchip do leitor, não. Guardar
    e buscar normalizado evita 'não encontrei' por causa de pontuação."""
    return re.sub(r"\D", "", value)


def _fold(value: str) -> str:
    """Contrapartida em Python do que o banco faz com unaccent(lower(...))."""
    return (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _folded(column: sa.ColumnElement[str]) -> sa.ColumnElement[str]:
    """Sem acento e sem caixa: quem digita "jose" tem de achar "José".

    Nome brasileiro tem acento, e ninguém digita acento com a mão na luva."""
    return sa.func.unaccent(sa.func.lower(column))


def _digits_only(column: sa.ColumnElement[str | None]) -> sa.ColumnElement[str]:
    return sa.func.regexp_replace(sa.func.coalesce(column, ""), r"\D", "", "g")


def normalize_tax_id(value: str | None) -> str | None:
    """Documento do responsável em forma canônica: alfanumérico, maiúsculo.

    Guardar "123.456.789-00" numa tela e "12345678900" noutra é o mesmo dado em
    dois formatos: quebra a busca por documento e deixa passar tutor duplicado.
    Não reduzimos a dígitos porque documento de outro país tem letra (ADR-0004)."""
    if value is None:
        return None
    limpo = re.sub(r"[^0-9A-Za-z]", "", value).upper()
    return limpo or None


class PatientSearchService:
    @staticmethod
    def validate_identifier(clinic: Clinic, kind: str, value: str) -> str:
        """Aceita só os tipos que o perfil da clínica declara, e devolve o valor
        normalizado. Veterinária conhece microchip; saúde humana conhece CPF."""
        profile = get_profile(clinic.compliance_profile)
        allowed = {item.kind: item for item in profile.patient_identifier_kinds}
        spec = allowed.get(kind)
        if spec is None:
            raise AppError(
                "identifier_kind_not_allowed",
                422,
                kind=kind,
                allowed=sorted(allowed),
            )
        normalized = normalize(value) if spec.pattern else value.strip()
        if spec.pattern and not re.fullmatch(spec.pattern, normalized):
            raise AppError("identifier_invalid", 422, kind=kind)
        if not normalized:
            raise AppError("identifier_invalid", 422, kind=kind)
        return normalized

    @staticmethod
    async def search(
        session: AsyncSession, *, clinic_id: uuid.UUID, query: str, limit: int = 20
    ) -> list[PatientHit]:
        """Uma caixa só: nome do paciente, identificador (microchip/CPF/CNS),
        nome do tutor ou documento do tutor. Quem atende não deve precisar saber
        em qual campo o dado foi guardado."""
        text = query.strip()
        if not text:
            return []
        digits = normalize(text)
        like = f"%{_fold(text)}%"

        conditions = [
            _folded(Patient.name).like(like),
            _folded(Owner.name).like(like),
        ]
        if digits:
            conditions.append(
                Patient.id.in_(
                    sa.select(PatientIdentifier.patient_id).where(
                        PatientIdentifier.clinic_id == clinic_id,
                        PatientIdentifier.value == digits,
                    )
                )
            )
            # O documento pode ter sido gravado com pontuação por outra tela;
            # comparar só os dígitos evita "não encontrei" por causa de um ponto.
            conditions.append(_digits_only(Owner.tax_id) == digits)

        rows = list(
            (
                await session.execute(
                    sa.select(Patient, Owner)
                    .join(Owner, Owner.id == Patient.owner_id)
                    .where(Patient.clinic_id == clinic_id, sa.or_(*conditions))
                    .order_by(Patient.name)
                    .limit(limit)
                )
            ).all()
        )
        if not rows:
            return []

        patient_ids = [patient.id for patient, _ in rows]

        identifiers: dict[uuid.UUID, list[PatientIdentifier]] = {}
        for identifier in (
            await session.execute(
                sa.select(PatientIdentifier).where(PatientIdentifier.patient_id.in_(patient_ids))
            )
        ).scalars():
            identifiers.setdefault(identifier.patient_id, []).append(identifier)

        # Já internado? Quem busca precisa saber se abre a ficha ou interna de novo.
        active = {
            patient_id: hospitalization_id
            for patient_id, hospitalization_id in (
                await session.execute(
                    sa.select(Hospitalization.patient_id, Hospitalization.id).where(
                        Hospitalization.clinic_id == clinic_id,
                        Hospitalization.patient_id.in_(patient_ids),
                        Hospitalization.status == "active",
                    )
                )
            ).all()
        }

        return [
            PatientHit(
                patient=patient,
                owner=owner,
                identifiers=identifiers.get(patient.id, []),
                active_hospitalization_id=active.get(patient.id),
            )
            for patient, owner in rows
        ]
