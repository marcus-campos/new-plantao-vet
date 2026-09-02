### Task 9: Prescriptions e as cerimônias default da clínica

**Files:**
- Create: `backend/app/models/prescription.py`
- Modify: `backend/app/models/__init__.py` (reexportar `Prescription`, `PrescriptionKind`, `PrescriptionCategory`, `Criticality`)
- Create: `backend/app/schemas/prescription.py`
- Modify: `backend/app/services/hospitalization.py` (acrescentar `create_default_prescriptions`)
- Create: `backend/app/api/routes/prescriptions.py`
- Create: `backend/alembic/versions/0006_prescriptions.py`
- Modify: `backend/app/api/routes/hospitalizations.py` (chamar as cerimônias na admissão; preencher `prescriptions` no detalhe)
- Modify: `backend/app/main.py` (incluir o router)
- Modify: `backend/tests/factories.py` (acrescentar `make_prescription`)
- Test: `backend/tests/test_prescriptions.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `translate` (Task 1), `Clinic.default_prescriptions`/`Clinic.locale` (Task 3), `AuditService` (Task 4), deps (Tasks 5–6), `Hospitalization`/`HospitalizationService` (Task 8).
- Produces:
  - Model `Prescription` + enums `PrescriptionKind` (`recurring|continuous|prn`), `PrescriptionCategory` (`medication|fluids|monitoring|nutrition|care|procedure`), `Criticality` (`normal|critical`); `UNIQUE (id, clinic_id)`.
  - `app.schemas.prescription.PrescriptionCreate` — valida por `kind` e resolve `tolerance_minutes` e `ends_at`.
  - `app.schemas.prescription.default_tolerance(criticality: str, frequency_minutes: int | None) -> int` — `critical` → 30; `normal` com `frequency_minutes >= 1440` → 120; senão 60.
  - `HospitalizationService.create_default_prescriptions(session, *, hospitalization, clinic, actor) -> list[Prescription]` — traduz `name_key` com o locale da clínica.
  - `POST /api/v1/hospitalizations/{id}/prescriptions` → 201 `PrescriptionOut`.
  - Factory `make_prescription(session, *, clinic, hospitalization=None, **overrides)`.
  - Migração `0006`.

> Achado clínico 1 (spec §9): a frequência é em **minutos**, não horas — monitoramento de UTI a cada 15–30 min precisa caber no schema. Achado 2: `category` existe desde já mesmo sem UI, porque jejum bloqueando alimentação e contador de dose por fármaco dependem dela.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_prescriptions.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Prescription
from app.schemas.prescription import default_tolerance
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_patient, make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="vet",
        license_number="12345", license_authority="CRMV-SP",
    )
    return clinic, membership


def test_tolerancia_default_por_criticidade():
    assert default_tolerance("critical", 480) == 30
    assert default_tolerance("normal", 480) == 60
    assert default_tolerance("normal", 1440) == 120
    assert default_tolerance("critical", 1440) == 30


async def test_prescricao_recorrente(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={
            "kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
            "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
            "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"},
            "price_minor": 1800,
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["tolerance_minutes"] == 60
    assert body["ends_at"] is not None
    assert body["first_dose_now"] is False


async def test_recorrente_sem_frequencia_e_recusada(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Sem frequência",
              "criticality": "normal"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_continua_exige_taxa(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    sem_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal", "details": {}},
        headers=bearer(personal_token(membership)),
    )
    assert sem_taxa.status_code == 422

    com_taxa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal",
              "details": {"rate_ml_h": 60}},
        headers=bearer(personal_token(membership)),
    )
    assert com_taxa.status_code == 201


async def test_prn_aceita_guardrails_e_dispensa_frequencia(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "prn", "category": "medication", "name": "Metadona 0,2 mg/kg IM",
              "criticality": "critical", "max_doses_24h": 4, "min_interval_minutes": 240,
              "details": {"drug": "metadona"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["frequency_minutes"] is None
    assert resp.json()["tolerance_minutes"] == 30


async def test_admissao_cria_cerimonias_no_locale_da_clinica(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership)),
    )).json()["hospitalization"]

    rows = list((await session.execute(
        sa.select(Prescription).where(Prescription.hospitalization_id == created["id"])
    )).scalars())
    nomes = sorted(row.name for row in rows)
    assert nomes == ["Contato com tutor", "Evolução diária"]
    assert all(row.category == "care" for row in rows)
    assert all(row.frequency_minutes == 1440 for row in rows)


async def test_cerimonias_em_ingles_quando_a_clinica_e_en(client, session):
    clinic = await make_clinic(session, slug="us-clinic", locale="en")
    clinic, membership = await _vet(session, clinic)
    patient = await make_patient(session, clinic=clinic)
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership)),
    )).json()["hospitalization"]

    rows = list((await session.execute(
        sa.select(Prescription).where(Prescription.hospitalization_id == created["id"])
    )).scalars())
    assert sorted(row.name for row in rows) == ["Daily progress note", "Owner contact"]


async def test_detalhe_da_internacao_traz_prescricoes(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {"drug": "dipirona"}},
        headers=token,
    )
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert [item["name"] for item in detail["prescriptions"]] == ["Dipirona"]


async def test_isolamento_de_tenant(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp_b.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Invasora",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_prescriptions.py -q`
Expected: `ImportError: cannot import name 'Prescription' from 'app.models'`.

- [ ] **Step 3: Implementar o model**

Crie `backend/app/models/prescription.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PrescriptionKind(StrEnum):
    recurring = "recurring"
    continuous = "continuous"
    prn = "prn"


class PrescriptionCategory(StrEnum):
    medication = "medication"
    fluids = "fluids"
    monitoring = "monitoring"
    nutrition = "nutrition"
    care = "care"
    procedure = "procedure"


class Criticality(StrEnum):
    normal = "normal"
    critical = "critical"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls, name=name, native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Prescription(Base):
    __tablename__ = "prescriptions"
    __table_args__ = (
        sa.UniqueConstraint("id", "clinic_id", name="uq_prescriptions_id_clinic"),
        sa.ForeignKeyConstraint(
            ["hospitalization_id", "clinic_id"],
            ["hospitalizations.id", "hospitalizations.clinic_id"],
            name="fk_prescriptions_hospitalization_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    kind: Mapped[PrescriptionKind] = mapped_column(_enum(PrescriptionKind, "prescription_kind"))
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    name: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # MINUTOS (achado clínico 1): UTI monitora a cada 15–60 min.
    frequency_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    duration_hours: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    criticality: Mapped[Criticality] = mapped_column(
        _enum(Criticality, "criticality"), default=Criticality.normal
    )
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    first_dose_now: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    is_controlled: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    max_doses_24h: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    min_interval_minutes: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
    starts_at: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    replaces_prescription_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("prescriptions.id"), default=None
    )
    suspended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    suspended_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
```

- [ ] **Step 4: Implementar os schemas com a validação por kind**

Crie `backend/app/schemas/prescription.py`:

```python
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CRITICAL_TOLERANCE = 30
NORMAL_TOLERANCE = 60
DAILY_TOLERANCE = 120
DAILY_FREQUENCY_MINUTES = 1440


def default_tolerance(criticality: str, frequency_minutes: int | None) -> int:
    """Janelas ISMP (spec §2): crítica 30; normal 60; normal diária ou mais espaçada 120."""
    if criticality == "critical":
        return CRITICAL_TOLERANCE
    if frequency_minutes is not None and frequency_minutes >= DAILY_FREQUENCY_MINUTES:
        return DAILY_TOLERANCE
    return NORMAL_TOLERANCE


class PrescriptionCreate(BaseModel):
    kind: Literal["recurring", "continuous", "prn"]
    category: Literal["medication", "fluids", "monitoring", "nutrition", "care", "procedure"]
    name: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    frequency_minutes: int | None = Field(default=None, ge=5)
    duration_hours: int | None = Field(default=None, ge=1)
    criticality: Literal["normal", "critical"] = "normal"
    tolerance_minutes: int | None = Field(default=None, ge=1)
    first_dose_now: bool = False
    is_controlled: bool = False
    max_doses_24h: int | None = Field(default=None, ge=1)
    min_interval_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    starts_at: datetime | None = None

    @model_validator(mode="after")
    def check_kind(self) -> "PrescriptionCreate":
        if self.kind in ("recurring", "continuous") and self.frequency_minutes is None:
            raise ValueError("frequency_minutes é obrigatório para recurring e continuous")
        if self.kind == "prn" and self.frequency_minutes is not None:
            raise ValueError("prn não tem agenda: frequency_minutes deve ficar vazio")
        if self.kind == "continuous" and "rate_ml_h" not in self.details:
            raise ValueError("continuous exige details.rate_ml_h")
        return self

    def resolved_tolerance(self) -> int:
        return self.tolerance_minutes or default_tolerance(self.criticality, self.frequency_minutes)

    def resolved_starts_at(self) -> datetime:
        return self.starts_at or datetime.now(UTC)

    def resolved_ends_at(self) -> datetime | None:
        if self.duration_hours is None:
            return None
        return self.resolved_starts_at() + timedelta(hours=self.duration_hours)


class PrescriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    kind: str
    category: str
    name: str
    details: dict[str, Any]
    frequency_minutes: int | None
    criticality: str
    tolerance_minutes: int
    first_dose_now: bool
    is_controlled: bool
    max_doses_24h: int | None
    min_interval_minutes: int | None
    price_minor: int | None
    starts_at: datetime
    ends_at: datetime | None
    replaces_prescription_id: uuid.UUID | None
    suspended_at: datetime | None
```

- [ ] **Step 5: Implementar as cerimônias default**

Acrescente a `backend/app/services/hospitalization.py`:

```python
from app.i18n.catalog import translate
from app.models import Prescription
from app.schemas.prescription import default_tolerance


class HospitalizationService:
    # Os métodos `admit` (Task 8) e `close` (Task 8) continuam onde estão;
    # acrescente APENAS o método abaixo à classe existente.

    @staticmethod
    async def create_default_prescriptions(
        session: AsyncSession, *, hospitalization: Hospitalization, clinic: Clinic, actor: ActorInfo
    ) -> list[Prescription]:
        """Cerimônias do dia (spec §2): nascem da admissão e reusam aprazamento,
        tolerância e auditoria — sem entidade nova. O `name_key` é conteúdo NOSSO,
        então é traduzido no locale da clínica; nome digitado pela clínica nunca é."""
        created: list[Prescription] = []
        for template in clinic.default_prescriptions:
            prescription = Prescription(
                clinic_id=clinic.id,
                hospitalization_id=hospitalization.id,
                kind=template.get("kind", "recurring"),
                category=template.get("category", "care"),
                name=translate(template["name_key"], clinic.locale),
                details={"anchor": template["anchor"]} if template.get("anchor") else {},
                frequency_minutes=template["frequency_minutes"],
                criticality=template.get("criticality", "normal"),
                tolerance_minutes=default_tolerance(
                    template.get("criticality", "normal"), template["frequency_minutes"]
                ),
                starts_at=hospitalization.admitted_at,
                created_by=actor.membership_id,
            )
            session.add(prescription)
            created.append(prescription)
        await session.flush()
        for prescription in created:
            await AuditService.record(
                session, clinic_id=clinic.id, actor=actor, action="prescription_created",
                entity_type="prescription", entity_id=prescription.id,
                after=AuditService.snapshot(prescription), extra={"source": "clinic_default"},
            )
        return created
```

- [ ] **Step 6: Chamar as cerimônias na admissão**

Em `backend/app/api/routes/hospitalizations.py`, dentro de `admit`, depois de `HospitalizationService.admit(...)` e antes do `commit`:

```python
    await HospitalizationService.create_default_prescriptions(
        session, hospitalization=hospitalization, clinic=clinic, actor=actor
    )
```

E no `detail`, preencha `prescriptions` (o campo que a Task 8 reservou):

```python
    prescriptions = list((await session.execute(
        sa.select(Prescription)
        .where(
            Prescription.hospitalization_id == hospitalization.id,
            Prescription.suspended_at.is_(None),
        )
        .order_by(Prescription.starts_at)
    )).scalars())
    return HospitalizationDetail(
        hospitalization=HospitalizationOut.model_validate(hospitalization),
        prescriptions=[PrescriptionOut.model_validate(row) for row in prescriptions],
    )
```

- [ ] **Step 7: Implementar o router de prescrições**

Crie `backend/app/api/routes/prescriptions.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Hospitalization, Prescription
from app.schemas.prescription import PrescriptionCreate, PrescriptionOut
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1", tags=["prescriptions"])


@router.post(
    "/hospitalizations/{hospitalization_id}/prescriptions",
    response_model=PrescriptionOut, status_code=201,
)
async def create_prescription(
    hospitalization_id: uuid.UUID,
    payload: PrescriptionCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    prescription = Prescription(
        clinic_id=auth.clinic_id,
        hospitalization_id=hospitalization.id,
        kind=payload.kind,
        category=payload.category,
        name=payload.name,
        details=payload.details,
        frequency_minutes=payload.frequency_minutes,
        duration_hours=payload.duration_hours,
        criticality=payload.criticality,
        tolerance_minutes=payload.resolved_tolerance(),
        first_dose_now=payload.first_dose_now,
        is_controlled=payload.is_controlled,
        max_doses_24h=payload.max_doses_24h,
        min_interval_minutes=payload.min_interval_minutes,
        price_minor=payload.price_minor,
        starts_at=payload.resolved_starts_at(),
        ends_at=payload.resolved_ends_at(),
        created_by=actor.membership_id,
    )
    session.add(prescription)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_created",
        entity_type="prescription", entity_id=prescription.id,
        after=AuditService.snapshot(prescription),
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)
```

Registre o router em `create_app()`.

- [ ] **Step 8: Migração, factory e verde**

Run: `uv run alembic revision --autogenerate -m "prescriptions"` → renomeie para `0006_prescriptions.py` (`revision="0006"`, `down_revision="0005"`).

Em `backend/tests/factories.py`:

```python
async def make_prescription(session, *, clinic, hospitalization=None, **overrides):
    hospitalization = hospitalization or await make_hospitalization(session, clinic=clinic)
    values = {
        "kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
        "details": {"drug": "dipirona"}, "frequency_minutes": 480, "criticality": "normal",
        "tolerance_minutes": 60, "starts_at": datetime.now(UTC), **overrides,
    }
    prescription = Prescription(
        clinic_id=clinic.id, hospitalization_id=hospitalization.id, **values
    )
    session.add(prescription)
    await session.flush()
    return prescription
```

Run: `uv run pytest tests/test_prescriptions.py -q && uv run pytest -q && uv run ruff check .`
Expected: `9 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/prescription.py backend/app/models/__init__.py backend/app/schemas/prescription.py backend/app/services/hospitalization.py backend/app/api/routes backend/alembic/versions/0006_prescriptions.py backend/app/main.py backend/tests
git commit -m "feat(prescricao): kinds, categorias, tolerancia ISMP e cerimonias default da clinica"
```

---

### Task 10: SchedulingService.generate — o aprazamento, puro e sem I/O

**Files:**
- Create: `backend/app/services/scheduling.py`
- Test: `backend/tests/test_scheduling.py`

**Interfaces:**
- Consumes: `Prescription` (Task 9), `Clinic` (`timezone`, `anchors`, `locale` — Task 3), `translate` (Task 1), `Task` (model da Task 11 — nesta task o serviço monta objetos `Task` **em memória**, sem persistir; a Task 11 os grava).
- Produces:
  - `app.services.scheduling.SchedulingService.generate(prescription, clinic, until) -> list[Task]` — assinatura EXATA do brief, função pura.
  - `app.services.scheduling.local_to_utc(day, hhmm, tzinfo) -> datetime` — conversão com tratamento de hora inexistente/ambígua (DST).

> Esta é a task mais densa em regra de negócio do plano e a que mais protege o produto: os horários de dose nascem aqui. Por ser pura, cada regra vira um teste com números concretos, sem banco e sem HTTP.

- [ ] **Step 1: Escrever os testes que falham (todas as 8 regras do contrato)**

Crie `backend/tests/test_scheduling.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Clinic, Prescription
from app.services.scheduling import SchedulingService

SP = ZoneInfo("America/Sao_Paulo")
ANCHORS = {"1440": ["10:00"], "720": ["10:00", "22:00"], "480": ["10:00", "18:00", "02:00"]}


def _clinic(timezone: str = "America/Sao_Paulo", locale: str = "pt-BR") -> Clinic:
    return Clinic(
        name="Demo", slug="demo", timezone=timezone, locale=locale,
        anchors=dict(ANCHORS), default_prescriptions=[],
    )


def _prescription(**overrides) -> Prescription:
    values = {
        "kind": "recurring", "category": "medication", "name": "Dipirona",
        "details": {}, "frequency_minutes": 480, "criticality": "normal",
        "tolerance_minutes": 60, "first_dose_now": False,
        "starts_at": datetime(2026, 9, 1, 17, 0, tzinfo=UTC),  # 14:00 em São Paulo
        "ends_at": None,
    }
    values.update(overrides)
    return Prescription(**values)


def _local_times(tasks, tz=SP):
    return [task.scheduled_for.astimezone(tz).strftime("%d/%m %H:%M") for task in tasks]


def test_ancoras_a_partir_das_14h():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 3, 3, 0, tzinfo=UTC)
    )
    assert _local_times(tasks)[:4] == ["01/09 18:00", "02/09 02:00", "02/09 10:00", "02/09 18:00"]


def test_rollover_quando_as_ancoras_do_dia_acabam():
    # Admissão às 23:00: não existe âncora >= 23:00 nesse dia; a próxima é 02:00 do dia seguinte.
    tasks = SchedulingService.generate(
        _prescription(starts_at=datetime(2026, 9, 2, 2, 0, tzinfo=UTC)),  # 23:00 do dia 01 em SP
        _clinic(), until=datetime(2026, 9, 2, 22, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[0] == "02/09 02:00"


def test_frequencia_sem_ancora_usa_offset():
    # q30min: monitoramento de UTI — não existe âncora para 30, então offset puro.
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=30),
        _clinic(), until=datetime(2026, 9, 1, 19, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == ["01/09 14:00", "01/09 14:30", "01/09 15:00", "01/09 15:30", "01/09 16:00"]


def test_primeira_dose_agora_suprime_a_ancora_proxima():
    # 14:00 + q8h com âncoras 10/18/02: a de 18:00 fica a 4h < (480 - 60)min → suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True),
        _clinic(), until=datetime(2026, 9, 2, 14, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:3] == ["01/09 14:00", "02/09 02:00", "02/09 10:00"]


def test_primeira_dose_agora_sem_supressao_quando_a_ancora_esta_longe():
    # 10:30 local: a âncora das 18:00 está a 7h30 >= (480-60)min → NÃO é suprimida.
    tasks = SchedulingService.generate(
        _prescription(first_dose_now=True, starts_at=datetime(2026, 9, 1, 13, 30, tzinfo=UTC)),
        _clinic(), until=datetime(2026, 9, 2, 4, 0, tzinfo=UTC),
    )
    assert _local_times(tasks)[:2] == ["01/09 10:30", "01/09 18:00"]


def test_ends_at_corta_o_horizonte():
    tasks = SchedulingService.generate(
        _prescription(ends_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC)),  # 02/09 00:00 em SP
        _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert _local_times(tasks) == ["01/09 18:00"]


def test_until_antes_do_inicio_devolve_vazio():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    )
    assert tasks == []


def test_prn_nao_gera_nada():
    tasks = SchedulingService.generate(
        _prescription(kind="prn", frequency_minutes=None),
        _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC),
    )
    assert tasks == []


def test_continua_gera_tarefa_de_checagem_traduzida():
    tasks = SchedulingService.generate(
        _prescription(kind="continuous", name="Ringer Lactato", frequency_minutes=120,
                      details={"rate_ml_h": 60}),
        _clinic(), until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Checagem: Ringer Lactato"
    assert tasks[0].category == "medication"


def test_continua_em_ingles():
    tasks = SchedulingService.generate(
        _prescription(kind="continuous", name="Lactated Ringer", frequency_minutes=120,
                      details={"rate_ml_h": 60}),
        _clinic(locale="en"), until=datetime(2026, 9, 1, 21, 0, tzinfo=UTC),
    )
    assert tasks[0].title == "Check: Lactated Ringer"


def test_horario_de_verao_nao_estoura_nem_duplica():
    # America/Santiago adianta o relógio em 06/09/2026: 00:00 não existe nesse dia.
    clinic = _clinic(timezone="America/Santiago")
    clinic.anchors = {"1440": ["00:00"]}
    tasks = SchedulingService.generate(
        _prescription(frequency_minutes=1440,
                      starts_at=datetime(2026, 9, 4, 12, 0, tzinfo=UTC)),
        clinic, until=datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
    )
    assert len(tasks) == len({task.scheduled_for for task in tasks})
    assert all(task.scheduled_for.tzinfo is not None for task in tasks)
    assert tasks == sorted(tasks, key=lambda task: task.scheduled_for)


def test_ordem_e_sempre_crescente():
    tasks = SchedulingService.generate(
        _prescription(), _clinic(), until=datetime(2026, 9, 5, 0, 0, tzinfo=UTC)
    )
    horarios = [task.scheduled_for for task in tasks]
    assert horarios == sorted(horarios)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `ModuleNotFoundError: No module named 'app.services.scheduling'`.

- [ ] **Step 3: Implementar a conversão local→UTC com DST**

Crie `backend/app/services/scheduling.py`:

```python
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.i18n.catalog import translate
from app.models import Clinic, Prescription, Task

HORIZON_STEP = timedelta(days=1)


def local_to_utc(day: date, hhmm: str, tz: ZoneInfo) -> datetime:
    """Converte 'HH:MM' de um dia local para UTC.

    Regra 7 do contrato: numa virada de horário de verão a hora local pode não
    existir (adiantamento) — nesse caso empurramos para a próxima hora válida —
    ou ser ambígua (atraso), quando usamos a primeira ocorrência (fold=0).
    """
    hour, minute = (int(part) for part in hhmm.split(":"))
    naive = datetime.combine(day, time(hour, minute))
    candidate = naive.replace(tzinfo=tz, fold=0)
    # Hora inexistente: o offset "salta", então a ida-e-volta não bate.
    if candidate.astimezone(ZoneInfo("UTC")).astimezone(tz).replace(tzinfo=None) != naive:
        candidate = (naive + timedelta(hours=1)).replace(tzinfo=tz, fold=0)
    return candidate.astimezone(ZoneInfo("UTC"))
```

- [ ] **Step 4: Rodar e ver falhar no generate**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `AttributeError: module 'app.services.scheduling' has no attribute 'SchedulingService'`.

- [ ] **Step 5: Implementar SchedulingService.generate**

Acrescente a `backend/app/services/scheduling.py`:

```python
class SchedulingService:
    @staticmethod
    def generate(prescription: Prescription, clinic: Clinic, until: datetime) -> list[Task]:
        """Contrato do brief, regras 1 a 8. Função PURA: monta objetos Task em
        memória (quem persiste é a Task 11) e não toca em banco nem em relógio."""
        if prescription.kind == "prn":  # regra 1
            return []

        horizon = until  # regra 2
        if prescription.ends_at is not None:
            horizon = min(horizon, prescription.ends_at)
        start = prescription.starts_at
        if horizon <= start and not prescription.first_dose_now:
            return []

        tz = ZoneInfo(clinic.timezone)
        anchors = clinic.anchors.get(str(prescription.frequency_minutes))
        moments: list[datetime] = []

        if prescription.first_dose_now:  # regra 5 (parte 1)
            moments.append(start)

        if anchors:  # regra 3
            ordered = sorted(anchors)
            day = start.astimezone(tz).date()
            # Uma volta a mais que o horizonte para cobrir o rollover do último dia.
            while True:
                for hhmm in ordered:
                    moment = local_to_utc(day, hhmm, tz)
                    if moment < start:
                        continue
                    if moment > horizon:
                        break
                    moments.append(moment)
                day += HORIZON_STEP
                if local_to_utc(day, ordered[0], tz) > horizon:
                    break
        else:  # regra 4
            step = timedelta(minutes=prescription.frequency_minutes)
            moment = start
            while moment <= horizon:
                if not (prescription.first_dose_now and moment == start):
                    moments.append(moment)
                moment += step

        if prescription.first_dose_now and anchors:  # regra 5 (parte 2)
            gap = timedelta(minutes=prescription.frequency_minutes - prescription.tolerance_minutes)
            moments = [
                moment for moment in moments
                if moment == start or moment - start >= gap
            ]

        title = prescription.name
        if prescription.kind == "continuous":  # regra 6
            title = translate("task.check", clinic.locale, name=prescription.name)

        return [
            Task(
                clinic_id=prescription.clinic_id,
                hospitalization_id=prescription.hospitalization_id,
                prescription_id=prescription.id,
                title=title,
                category=prescription.category,
                scheduled_for=moment,
                criticality=prescription.criticality,
                tolerance_minutes=prescription.tolerance_minutes,
                price_minor=prescription.price_minor,
            )
            for moment in sorted(set(moments))
        ]
```

> A regra 8 (idempotência por `INSERT ... ON CONFLICT`) e o congelamento de `scheduled_for` são responsabilidade de quem persiste — Task 11.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_scheduling.py -q`
Expected: `12 passed`.

> Se `test_horario_de_verao_nao_estoura_nem_duplica` falhar, a causa quase certa é `local_to_utc`: confira a detecção de hora inexistente antes de mexer no `generate`.

- [ ] **Step 7: Suíte e lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: suíte verde, lint limpo.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/scheduling.py backend/tests/test_scheduling.py
git commit -m "feat(aprazamento): horarios por ancora com rollover, primeira dose imediata e DST"
```

---

### Task 11: Persistência idempotente das tarefas e o worker de 48h

**Files:**
- Create: `backend/app/models/task.py`
- Modify: `backend/app/models/__init__.py` (reexportar `Task`, `TaskStatus`)
- Create: `backend/app/services/tasks.py`
- Create: `backend/app/workers/scheduler.py`
- Create: `backend/alembic/versions/0007_tasks.py`
- Modify: `backend/app/api/routes/prescriptions.py` (persistir as tarefas ao criar a prescrição)
- Modify: `backend/app/services/hospitalization.py` (persistir as tarefas das cerimônias)
- Test: `backend/tests/test_task_persistence.py`

**Interfaces:**
- Consumes: `SchedulingService.generate` (Task 10), `Prescription` (Task 9), `Hospitalization` (Task 8), `AuditService` (Task 4).
- Produces:
  - Model `Task` + enum `TaskStatus` (`pending|done|partial|not_done|cancelled`), com índice `(clinic_id, status, scheduled_for)`, UNIQUE parcial `(prescription_id, scheduled_for)` e FK composta `(prescription_id, clinic_id)`.
  - `app.services.tasks.TaskService.materialize(session, *, prescription, clinic, until) -> int` — gera e grava com `ON CONFLICT DO NOTHING`; devolve quantas linhas nasceram.
  - `app.workers.scheduler.extend_scheduling_window(session_factory, *, now, horizon_hours=48) -> int` — o job único; carrega cada prescrição ativa com `FOR UPDATE` e re-filtra dentro da transação.
  - `app.workers.scheduler.build_scheduler(session_factory) -> AsyncIOScheduler`.
  - Migração `0007`.

> Achado de engenharia 2 (spec §9): sem o índice único, "idempotência por SELECT-antes-de-INSERT" quebra em corrida. E no GCP o serviço que hospeda o scheduler precisa de `max-instances=1` com CPU always-allocated, ou o job toma `pg_advisory_lock` — Cloud Run escala e rodaria dois schedulers (tratar na semana 4).

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_task_persistence.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Task
from app.services.tasks import TaskService
from app.workers.scheduler import extend_scheduling_window
from tests.factories import make_clinic, make_hospitalization, make_prescription


async def _count(session, prescription_id) -> int:
    return await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(Task.prescription_id == prescription_id)
    )


async def test_materialize_grava_a_janela(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), frequency_minutes=480
    )
    criadas = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic,
        until=datetime.now(UTC) + timedelta(hours=48),
    )
    assert criadas >= 5
    assert await _count(session, prescription.id) == criadas


async def test_rodar_duas_vezes_nao_duplica(session):
    clinic = await make_clinic(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    until = datetime.now(UTC) + timedelta(hours=48)

    primeira = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    segunda = await TaskService.materialize(
        session, prescription=prescription, clinic=clinic, until=until
    )
    assert segunda == 0
    assert await _count(session, prescription.id) == primeira


async def test_job_estende_a_janela_sem_duplicar(session, db_session_factory):
    clinic = await make_clinic(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    await session.commit()

    primeira = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    segunda = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert primeira > 0
    assert segunda == 0


async def test_job_ignora_prescricao_suspensa(session, db_session_factory):
    clinic = await make_clinic(session)
    prescription = await make_prescription(
        session, clinic=clinic, starts_at=datetime.now(UTC), suspended_at=datetime.now(UTC)
    )
    await session.commit()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_job_ignora_internacao_encerrada(session, db_session_factory):
    clinic = await make_clinic(session)
    hosp = await make_hospitalization(session, clinic=clinic, status="discharged")
    await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=datetime.now(UTC)
    )
    await session.commit()
    assert await extend_scheduling_window(db_session_factory, now=datetime.now(UTC)) == 0


async def test_prescrever_pela_api_ja_cria_as_tarefas(client, session):
    from tests.factories import make_membership, make_user
    from tests.helpers import bearer, personal_token

    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)

    resp = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {"drug": "dipirona"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert await _count(session, resp.json()["id"]) > 0
```

Acrescente ao `backend/tests/conftest.py` a fixture que o job precisa (ele abre a própria sessão):

```python
@pytest.fixture
def db_session_factory(db_session):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        # O job roda dentro da MESMA transação do teste — o rollback do harness
        # continua limpando tudo no fim.
        yield db_session

    return _factory
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_persistence.py -q`
Expected: `ImportError: cannot import name 'Task' from 'app.models'`.

- [ ] **Step 3: Implementar o model Task**

Crie `backend/app/models/task.py`:

```python
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.prescription import Criticality, PrescriptionCategory, _enum


class TaskStatus(StrEnum):
    pending = "pending"
    done = "done"
    partial = "partial"
    not_done = "not_done"
    cancelled = "cancelled"


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        sa.Index("ix_tasks_clinic_status_scheduled", "clinic_id", "status", "scheduled_for"),
        # Idempotência do aprazamento (regra 8 do contrato): a corrida entre a
        # criação da prescrição e o job de 48h é resolvida no banco, não em código.
        sa.Index(
            "uq_tasks_prescription_scheduled", "prescription_id", "scheduled_for",
            unique=True, postgresql_where=sa.text("prescription_id IS NOT NULL"),
        ),
        sa.ForeignKeyConstraint(
            ["prescription_id", "clinic_id"],
            ["prescriptions.id", "prescriptions.clinic_id"],
            name="fk_tasks_prescription_tenant",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    hospitalization_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("hospitalizations.id"), index=True
    )
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, default=None)
    title: Mapped[str] = mapped_column(sa.Text)
    category: Mapped[PrescriptionCategory] = mapped_column(
        _enum(PrescriptionCategory, "prescription_category")
    )
    scheduled_for: Mapped[datetime] = mapped_column(sa.TIMESTAMP(timezone=True))
    criticality: Mapped[Criticality] = mapped_column(_enum(Criticality, "criticality"))
    tolerance_minutes: Mapped[int] = mapped_column(sa.Integer)
    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), default=TaskStatus.pending
    )
    executed_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("memberships.id"), default=None
    )
    retroactive: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    early: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    outcome_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
    values: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    price_minor: Mapped[int | None] = mapped_column(sa.Integer, default=None)
```

- [ ] **Step 4: Implementar TaskService.materialize**

Crie `backend/app/services/tasks.py`:

```python
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, Prescription, Task
from app.services.scheduling import SchedulingService


class TaskService:
    @staticmethod
    async def materialize(
        session: AsyncSession, *, prescription: Prescription, clinic: Clinic, until: datetime
    ) -> int:
        """Grava a janela de tarefas da prescrição. Idempotente pelo índice único
        parcial (prescription_id, scheduled_for): o segundo INSERT não faz nada."""
        candidates = SchedulingService.generate(prescription, clinic, until)
        if not candidates:
            return 0
        rows = [
            {
                "id": task.id,
                "clinic_id": task.clinic_id,
                "hospitalization_id": task.hospitalization_id,
                "prescription_id": task.prescription_id,
                "title": task.title,
                "category": task.category,
                "scheduled_for": task.scheduled_for,
                "criticality": task.criticality,
                "tolerance_minutes": task.tolerance_minutes,
                "status": "pending",
                "price_minor": task.price_minor,
            }
            for task in candidates
        ]
        stmt = (
            insert(Task)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["prescription_id", "scheduled_for"])
            .returning(Task.id)
        )
        created = list((await session.execute(stmt)).scalars())
        await session.flush()
        return len(created)
```

- [ ] **Step 5: Implementar o worker**

Crie `backend/app/workers/scheduler.py`:

```python
from datetime import datetime, timedelta

import sqlalchemy as sa
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.models import Clinic, Hospitalization, Prescription
from app.services.tasks import TaskService

HORIZON_HOURS = 48


async def extend_scheduling_window(session_factory, *, now: datetime, horizon_hours: int = HORIZON_HOURS) -> int:
    """Job ÚNICO do sistema. Não existe verificador de atraso: 'atrasada' é
    computada na leitura (TaskService.display_state), então nada a fazer aqui."""
    until = now + timedelta(hours=horizon_hours)
    created = 0
    async with session_factory() as session:
        stmt = (
            sa.select(Prescription.id)
            .join(Hospitalization, Hospitalization.id == Prescription.hospitalization_id)
            .where(
                Prescription.suspended_at.is_(None),
                Prescription.kind != "prn",
                sa.or_(Prescription.ends_at.is_(None), Prescription.ends_at > now),
                Hospitalization.status == "active",
            )
        )
        for prescription_id in list((await session.execute(stmt)).scalars()):
            # FOR UPDATE serializa com POST /prescriptions/{id}/suspend (Task 12):
            # sem isso, o job pode ressuscitar tarefas de uma prescrição suspensa.
            locked = await session.scalar(
                sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
            )
            if locked is None or locked.suspended_at is not None:
                continue
            hospitalization = await session.get(Hospitalization, locked.hospitalization_id)
            if hospitalization is None or hospitalization.status != "active":
                continue
            clinic = await session.get(Clinic, locked.clinic_id)
            created += await TaskService.materialize(
                session, prescription=locked, clinic=clinic, until=until
            )
        await session.commit()
    return created


def build_scheduler(session_factory) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        lambda: extend_scheduling_window(session_factory, now=datetime.now(UTC)),
        trigger="interval", hours=1, id="extend_scheduling_window", max_instances=1,
    )
    return scheduler
```

> **Deploy (semana 4):** Cloud Run escala horizontalmente e rodaria um scheduler por instância. O serviço que hospeda este job vai com `max-instances=1` e CPU always-allocated, ou o job passa a tomar `pg_advisory_lock` antes de rodar.

- [ ] **Step 6: Persistir ao prescrever**

Em `backend/app/api/routes/prescriptions.py`, depois do `AuditService.record` e antes do `commit`:

```python
    clinic = await session.get(Clinic, auth.clinic_id)
    await TaskService.materialize(
        session, prescription=prescription, clinic=clinic,
        until=datetime.now(UTC) + timedelta(hours=48),
    )
```

Faça o mesmo em `HospitalizationService.create_default_prescriptions`, para cada cerimônia criada.

- [ ] **Step 7: Migração e verde**

Run: `uv run alembic revision --autogenerate -m "tasks"` → renomeie para `0007_tasks.py` (`revision="0007"`, `down_revision="0006"`). **Confira à mão** que o índice único parcial saiu com `postgresql_where` — o autogenerate às vezes o omite.

Run: `uv run pytest tests/test_task_persistence.py -q && uv run pytest -q && uv run ruff check .`
Expected: `6 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 8: Commit**

```bash
git add backend/app/models/task.py backend/app/models/__init__.py backend/app/services/tasks.py backend/app/workers backend/alembic/versions/0007_tasks.py backend/app/api/routes/prescriptions.py backend/app/services/hospitalization.py backend/tests
git commit -m "feat(tarefas): persistencia idempotente da janela de 48h e worker unico"
```

---

### Task 12: Suspender, ajustar (titulação) e encerrar a internação cancelando o futuro

**Files:**
- Modify: `backend/app/api/routes/prescriptions.py` (rotas `suspend` e `adjust`)
- Modify: `backend/app/services/hospitalization.py` (cancelamento no `close`)
- Modify: `backend/app/api/routes/hospitalizations.py` (guarda `confirm_pending_tasks`)
- Modify: `backend/app/schemas/prescription.py` (`PrescriptionAdjust`)
- Test: `backend/tests/test_prescription_lifecycle.py`

**Interfaces:**
- Consumes: `Prescription`/`Task` (Tasks 9, 11), `TaskService.materialize` (Task 11), `AuditService` (Task 4), `HospitalizationService.close` (Task 8).
- Produces:
  - `POST /api/v1/prescriptions/{id}/suspend` → 200 `PrescriptionOut`.
  - `POST /api/v1/prescriptions/{id}/adjust` → 201 `PrescriptionOut` (a nova versão, com `replaces_prescription_id`).
  - `app.services.tasks.TaskService.cancel_future(session, *, clinic_id, prescription_id=None, hospitalization_id=None, now) -> int`.
  - `POST /api/v1/hospitalizations/{id}/outcome` completo: conta pendentes, exige `confirm_pending_tasks`, cancela futuras.

> Achado clínico 4 (spec §9): titular fluidoterapia é rotina. Sem `adjust`, cada ajuste de taxa viraria um par suspender+criar sem vínculo, e a pergunta "qual a taxa atual e qual era antes?" ficaria sem resposta na auditoria.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_prescription_lifecycle.py`:

```python
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import Prescription, Task
from app.workers.scheduler import extend_scheduling_window
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    return clinic, membership


async def test_suspender_preserva_o_passado_e_cancela_o_futuro(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    created = (await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
              "details": {"drug": "dipirona"}},
        headers=token,
    )).json()

    tarefas = list((await session.execute(
        sa.select(Task).where(Task.prescription_id == created["id"]).order_by(Task.scheduled_for)
    )).scalars())
    for tarefa in tarefas[:3]:
        tarefa.status = "done"
        tarefa.executed_at = datetime.now(UTC)
    await session.flush()

    resp = await client.post(f"/api/v1/prescriptions/{created['id']}/suspend", headers=token)
    assert resp.status_code == 200
    assert resp.json()["suspended_at"] is not None

    await session.refresh(tarefas[0])
    depois = list((await session.execute(
        sa.select(Task).where(Task.prescription_id == created["id"]).order_by(Task.scheduled_for)
    )).scalars())
    assert [t.status for t in depois[:3]] == ["done", "done", "done"]
    assert all(t.status == "cancelled" for t in depois[3:])


async def test_job_nao_ressuscita_tarefa_de_prescricao_suspensa(client, session, db_session_factory):
    clinic, membership = await _vet(session)
    prescription = await make_prescription(session, clinic=clinic, starts_at=datetime.now(UTC))
    token = bearer(personal_token(membership))
    await client.post(f"/api/v1/prescriptions/{prescription.id}/suspend", headers=token)
    await session.commit()

    criadas = await extend_scheduling_window(db_session_factory, now=datetime.now(UTC))
    assert criadas == 0
    pendentes = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == prescription.id, Task.status == "pending"
        )
    )
    assert pendentes == 0


async def test_ajustar_taxa_de_fluido_mantem_historico(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    original = (await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "continuous", "category": "fluids", "name": "Ringer Lactato",
              "frequency_minutes": 120, "criticality": "normal",
              "details": {"rate_ml_h": 60}},
        headers=token,
    )).json()

    ajustada = await client.post(
        f"/api/v1/prescriptions/{original['id']}/adjust",
        json={"details": {"rate_ml_h": 40}}, headers=token,
    )
    assert ajustada.status_code == 201
    nova = ajustada.json()
    assert nova["replaces_prescription_id"] == original["id"]
    assert nova["details"]["rate_ml_h"] == 40
    assert nova["suspended_at"] is None

    antiga = await session.get(Prescription, original["id"])
    assert antiga.suspended_at is not None

    futuras_antigas = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == antiga.id, Task.status == "pending"
        )
    )
    assert futuras_antigas == 0
    futuras_novas = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.prescription_id == uuid.UUID(nova["id"]), Task.status == "pending"
        )
    )
    assert futuras_novas > 0


async def test_alta_com_pendencias_exige_confirmacao(client, session):
    clinic, membership = await _vet(session)
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=token,
    )

    recusa = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged"}, headers=token,
    )
    assert recusa.status_code == 409
    assert recusa.json()["error"]["code"] == "pending_tasks_confirmation_required"
    assert recusa.json()["error"]["params"]["pending"] > 0

    ok = await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/outcome",
        json={"outcome": "discharged", "confirm_pending_tasks": True}, headers=token,
    )
    assert ok.status_code == 200
    restantes = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.hospitalization_id == hosp.id, Task.status == "pending"
        )
    )
    assert restantes == 0
```

Acrescente `import uuid` ao topo do arquivo de teste.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_prescription_lifecycle.py -q`
Expected: `404` nas rotas `suspend`/`adjust` (ainda não existem).

- [ ] **Step 3: Implementar o cancelamento em lote**

Acrescente a `backend/app/services/tasks.py`:

```python
    @staticmethod
    async def cancel_future(
        session: AsyncSession, *, clinic_id: uuid.UUID, now: datetime,
        prescription_id: uuid.UUID | None = None,
        hospitalization_id: uuid.UUID | None = None,
    ) -> int:
        """Cancela tarefas pendentes futuras. Executadas nunca são tocadas —
        o passado do prontuário é imutável (ADR-0003)."""
        stmt = (
            sa.update(Task)
            .where(
                Task.clinic_id == clinic_id,
                Task.status == "pending",
                Task.scheduled_for > now,
            )
            .values(status="cancelled")
            .returning(Task.id)
        )
        if prescription_id is not None:
            stmt = stmt.where(Task.prescription_id == prescription_id)
        if hospitalization_id is not None:
            stmt = stmt.where(Task.hospitalization_id == hospitalization_id)
        return len(list((await session.execute(stmt)).scalars()))
```

- [ ] **Step 4: Implementar suspend e adjust**

Acrescente a `backend/app/schemas/prescription.py`:

```python
class PrescriptionAdjust(BaseModel):
    """Titulação: o que muda na nova versão. O que não vier é herdado da anterior."""
    name: str | None = None
    details: dict[str, Any] | None = None
    frequency_minutes: int | None = Field(default=None, ge=5)
    criticality: Literal["normal", "critical"] | None = None
    tolerance_minutes: int | None = Field(default=None, ge=1)
    price_minor: int | None = Field(default=None, ge=0)
    reason: str | None = None
```

Acrescente a `backend/app/api/routes/prescriptions.py`:

```python
@router.post("/prescriptions/{prescription_id}/suspend", response_model=PrescriptionOut)
async def suspend(
    prescription_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    # FOR UPDATE serializa com o job de 48h: sem isso o worker pode inserir
    # tarefas futuras logo depois do cancelamento.
    prescription = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    before = AuditService.snapshot(prescription)
    now = datetime.now(UTC)
    prescription.suspended_at = now
    prescription.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, prescription_id=prescription.id, now=now
    )
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_suspended",
        entity_type="prescription", entity_id=prescription.id,
        before=before, after=AuditService.snapshot(prescription),
        extra={"cancelled_tasks": cancelled},
    )
    await session.commit()
    return PrescriptionOut.model_validate(prescription)


@router.post("/prescriptions/{prescription_id}/adjust", response_model=PrescriptionOut, status_code=201)
async def adjust(
    prescription_id: uuid.UUID,
    payload: PrescriptionAdjust,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrescriptionOut:
    await get_tenant_obj(session, Prescription, prescription_id, auth.clinic_id)
    previous = await session.scalar(
        sa.select(Prescription).where(Prescription.id == prescription_id).with_for_update()
    )
    now = datetime.now(UTC)
    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})

    replacement = Prescription(
        clinic_id=previous.clinic_id,
        hospitalization_id=previous.hospitalization_id,
        kind=previous.kind,
        category=previous.category,
        name=changes.get("name", previous.name),
        details=changes.get("details", previous.details),
        frequency_minutes=changes.get("frequency_minutes", previous.frequency_minutes),
        duration_hours=previous.duration_hours,
        criticality=changes.get("criticality", previous.criticality),
        tolerance_minutes=changes.get("tolerance_minutes", previous.tolerance_minutes),
        first_dose_now=False,
        is_controlled=previous.is_controlled,
        max_doses_24h=previous.max_doses_24h,
        min_interval_minutes=previous.min_interval_minutes,
        price_minor=changes.get("price_minor", previous.price_minor),
        starts_at=now,
        ends_at=previous.ends_at,
        replaces_prescription_id=previous.id,
        created_by=actor.membership_id,
    )
    session.add(replacement)

    before = AuditService.snapshot(previous)
    previous.suspended_at = now
    previous.suspended_by = actor.membership_id
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, prescription_id=previous.id, now=now
    )
    await session.flush()

    clinic = await session.get(Clinic, auth.clinic_id)
    await TaskService.materialize(
        session, prescription=replacement, clinic=clinic, until=now + timedelta(hours=48)
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="prescription_adjusted",
        entity_type="prescription", entity_id=replacement.id,
        before=before, after=AuditService.snapshot(replacement),
        extra={"replaces": str(previous.id), "cancelled_tasks": cancelled, "reason": payload.reason},
    )
    await session.commit()
    return PrescriptionOut.model_validate(replacement)
```

- [ ] **Step 5: Completar o desfecho da internação**

Em `backend/app/api/routes/hospitalizations.py`, dentro de `close`, antes de chamar o serviço:

```python
    now = datetime.now(UTC)
    pending = await session.scalar(
        sa.select(sa.func.count()).select_from(Task).where(
            Task.hospitalization_id == hospitalization.id,
            Task.status == "pending",
            Task.scheduled_for > now,
        )
    )
    if pending and not payload.confirm_pending_tasks:
        raise AppError("pending_tasks_confirmation_required", 409, pending=pending)
```

E depois de `HospitalizationService.close(...)`:

```python
    cancelled = await TaskService.cancel_future(
        session, clinic_id=auth.clinic_id, hospitalization_id=hospitalization.id, now=now
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="tasks_cancelled_on_outcome",
        entity_type="hospitalization", entity_id=hospitalization.id,
        extra={"cancelled_tasks": cancelled},
    )
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_prescription_lifecycle.py -q && uv run pytest -q && uv run ruff check .`
Expected: `4 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/routes backend/app/services backend/app/schemas/prescription.py backend/tests
git commit -m "feat(prescricao): suspensao, titulacao versionada e cancelamento no desfecho"
```

---
