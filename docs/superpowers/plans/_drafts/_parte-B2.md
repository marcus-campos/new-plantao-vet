### Task 7: Owners, Patients e Kennels (CRUD sem DELETE, paginado e auditado)

**Files:**
- Create: `backend/app/models/kennel.py`
- Create: `backend/app/models/owner.py`
- Create: `backend/app/models/patient.py`
- Modify: `backend/app/models/__init__.py` (reexportar `Kennel`, `Owner`, `Patient`)
- Create: `backend/app/schemas/pagination.py`
- Create: `backend/app/schemas/kennel.py`
- Create: `backend/app/schemas/owner.py`
- Create: `backend/app/schemas/patient.py`
- Create: `backend/app/api/routes/kennels.py`
- Create: `backend/app/api/routes/owners.py`
- Create: `backend/app/api/routes/patients.py`
- Create: `backend/alembic/versions/0004_kennels_owners_patients.py`
- Modify: `backend/app/main.py` (incluir os três routers)
- Modify: `backend/tests/factories.py` (acrescentar `make_kennel`, `make_owner`, `make_patient`)
- Test: `backend/tests/test_owners_patients_kennels.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `Base` (Task 2), `AuditService.record`/`AuditService.snapshot`/`ActorInfo` (Task 4), `get_session`/`get_current_auth`/`get_operator`/`get_tenant_obj` (Tasks 5–6), fixtures `client`/`session` e factories `make_clinic`/`make_user`/`make_membership` (Tasks 2–3), helpers `bearer`/`personal_token` (Task 5).
- Produces:
  - Models `Kennel` (`id, clinic_id, name, area, is_active`), `Owner` (`id, clinic_id, name, phone_e164, tax_id, whatsapp_opt_in_at, is_active`), `Patient` (`id, clinic_id, owner_id, name, species, breed, weight_kg, notes, is_active`).
  - `app.schemas.pagination.Page[T]` — `{"items": [...], "next_cursor": str | None}` — e `paginate(session, stmt, *, limit, cursor, id_column) -> Page` (ordena por `id` asc, `next_cursor` = id do último item quando há mais).
  - Rotas `GET/POST/PATCH /api/v1/kennels`, `/api/v1/owners`, `/api/v1/patients` — sem DELETE (regra transversal 3); `?limit=` default 50, máx 200; `?cursor=`.
  - Factories `make_kennel(session, *, clinic, **overrides)`, `make_owner(session, *, clinic, **overrides)`, `make_patient(session, *, clinic, owner=None, **overrides)`.
  - Migração `0004`.

> Regras transversais 2 e 4 aparecem aqui pela primeira vez em rota de domínio: `POST /patients` com `owner_id` de outra clínica devolve **404 `not_found`**, e cada router tem teste de isolamento em leitura e em FK de body.

- [ ] **Step 1: Escrever os testes que falham (CRUD, paginação, auditoria, tenancy)**

Crie `backend/tests/test_owners_patients_kennels.py`:

```python
import uuid
from decimal import Decimal

import sqlalchemy as sa

from app.models import AuditEntry, Owner, Patient
from tests.factories import make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer, personal_token


async def _vet(session, clinic=None):
    clinic = clinic or await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="vet",
        license_number="12345", license_authority="CRMV-SP",
    )
    return clinic, membership


async def test_criar_e_listar_kennel(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/kennels", json={"name": "UTI 03", "area": "UTI"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["name"] == "UTI 03"
    assert resp.json()["is_active"] is True

    listing = await client.get("/api/v1/kennels", headers=bearer(personal_token(membership)))
    assert listing.status_code == 200
    assert [item["name"] for item in listing.json()["items"]] == ["UTI 03"]
    assert listing.json()["next_cursor"] is None


async def test_criar_owner_e_patient_vinculado(client, session):
    clinic, membership = await _vet(session)
    token = bearer(personal_token(membership))
    owner_resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina Campos", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=token,
    )
    assert owner_resp.status_code == 201
    owner_id = owner_resp.json()["id"]

    patient_resp = await client.post(
        "/api/v1/patients",
        json={"name": "Thor", "species": "dog", "owner_id": owner_id, "weight_kg": "24.3"},
        headers=token,
    )
    assert patient_resp.status_code == 201
    assert patient_resp.json()["owner_id"] == owner_id
    assert patient_resp.json()["weight_kg"] == "24.3"


async def test_patch_de_peso_e_auditado_com_before_e_after(client, session):
    clinic, membership = await _vet(session)
    owner = await make_owner(session, clinic=clinic)
    patient = await make_patient(session, clinic=clinic, owner=owner, weight_kg=Decimal("24.3"))

    resp = await client.patch(
        f"/api/v1/patients/{patient.id}", json={"weight_kg": "25.1"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["weight_kg"] == "25.1"

    entry = (
        await session.execute(
            sa.select(AuditEntry)
            .where(AuditEntry.entity_type == "patient", AuditEntry.action == "patient_updated")
            .order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert entry.payload["before"]["weight_kg"] == "24.3"
    assert entry.payload["after"]["weight_kg"] == "25.1"
    assert entry.actor_license == "12345"


async def test_snapshot_do_owner_nao_carrega_contato(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/owners",
        json={"name": "Marina", "phone_e164": "+5511999990000", "tax_id": "12345678900"},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201

    entry = (
        await session.execute(
            sa.select(AuditEntry).where(AuditEntry.entity_type == "owner").order_by(AuditEntry.id.desc())
        )
    ).scalars().first()
    assert entry is not None
    assert "phone_e164" not in entry.payload["after"]
    assert "tax_id" not in entry.payload["after"]
    assert entry.payload["after"]["name"] == "Marina"


async def test_paginacao_por_cursor_percorre_tudo_sem_repetir(client, session):
    clinic, membership = await _vet(session)
    for index in range(5):
        await make_owner(session, clinic=clinic, name=f"Tutor {index}")
    token = bearer(personal_token(membership))

    seen: list[str] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/owners?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=token)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5
    assert len(set(seen)) == 5


async def test_limite_maximo_de_paginacao(client, session):
    clinic, membership = await _vet(session)
    resp = await client.get("/api/v1/owners?limit=500", headers=bearer(personal_token(membership)))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_desativacao_em_vez_de_delete(client, session):
    clinic, membership = await _vet(session)
    kennel = await make_kennel(session, clinic=clinic)
    token = bearer(personal_token(membership))

    assert (await client.delete(f"/api/v1/kennels/{kennel.id}", headers=token)).status_code == 405

    resp = await client.patch(f"/api/v1/kennels/{kennel.id}", json={"is_active": False}, headers=token)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    listing = (await client.get("/api/v1/kennels", headers=token)).json()
    assert listing["items"] == []
    todos = (await client.get("/api/v1/kennels?include_inactive=true", headers=token)).json()
    assert len(todos["items"]) == 1


async def test_isolamento_de_tenant_na_leitura(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.get(f"/api/v1/owners/{owner_b.id}", headers=bearer(personal_token(membership_a)))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    owner_b = await make_owner(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Invasor", "species": "dog", "owner_id": str(owner_b.id)},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_patient_de_owner_inexistente_e_404(client, session):
    clinic, membership = await _vet(session)
    resp = await client.post(
        "/api/v1/patients",
        json={"name": "Fantasma", "species": "cat", "owner_id": str(uuid.uuid4())},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Rodar e ver falhar**

Run (em `backend/`): `uv run pytest tests/test_owners_patients_kennels.py -q`
Expected: erro de coleta — `ImportError: cannot import name 'Owner' from 'app.models'`.

- [ ] **Step 3: Implementar os três models**

Crie `backend/app/models/kennel.py`:

```python
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Kennel(Base):
    __tablename__ = "kennels"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(sa.Text)
    area: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Crie `backend/app/models/owner.py`:

```python
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Owner(Base):
    __tablename__ = "owners"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    name: Mapped[str] = mapped_column(sa.Text)
    # E.164 (+5511999990000): pré-requisito do WhatsApp internacional (spec §2).
    phone_e164: Mapped[str] = mapped_column(sa.Text)
    tax_id: Mapped[str | None] = mapped_column(sa.Text, default=None)
    whatsapp_opt_in_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=None
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Crie `backend/app/models/patient.py`:

```python
import uuid
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Patient(Base):
    __tablename__ = "patients"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("owners.id"))
    name: Mapped[str] = mapped_column(sa.Text)
    species: Mapped[str] = mapped_column(sa.Text)
    breed: Mapped[str | None] = mapped_column(sa.Text, default=None)
    # SEMPRE em kg (unidade SI). clinics.unit_system decide a exibição — ADR-0004.
    weight_kg: Mapped[Decimal | None] = mapped_column(sa.Numeric(6, 3), default=None)
    notes: Mapped[str | None] = mapped_column(sa.Text, default=None)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True)
```

Acrescente os três a `backend/app/models/__init__.py` (imports e `__all__`).

- [ ] **Step 4: Implementar o envelope de paginação**

Crie `backend/app/schemas/pagination.py`:

```python
from typing import Any, Generic, TypeVar

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


async def paginate(
    session: AsyncSession,
    stmt: sa.Select,
    *,
    id_column: Any,
    limit: int,
    cursor: str | None,
    descending: bool = False,
) -> tuple[list[Any], str | None]:
    """Paginação por cursor: pede limit+1 linhas para saber se há próxima página."""
    if cursor is not None:
        stmt = stmt.where(id_column < cursor if descending else id_column > cursor)
    stmt = stmt.order_by(id_column.desc() if descending else id_column.asc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    if len(rows) > limit:
        rows = rows[:limit]
        return rows, str(rows[-1].id)
    return rows, None
```

- [ ] **Step 5: Rodar e ver falhar por rota ausente**

Run: `uv run pytest tests/test_owners_patients_kennels.py -q`
Expected: falhas com `404` nas rotas (models importam, rotas ainda não existem).

- [ ] **Step 6: Implementar os schemas**

Crie `backend/app/schemas/owner.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OwnerCreate(BaseModel):
    name: str = Field(min_length=1)
    # E.164: '+' seguido de 8 a 15 dígitos.
    phone_e164: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None


class OwnerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    phone_e164: str | None = Field(default=None, pattern=r"^\+[1-9]\d{7,14}$")
    tax_id: str | None = None
    whatsapp_opt_in_at: datetime | None = None
    is_active: bool | None = None


class OwnerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone_e164: str
    tax_id: str | None
    whatsapp_opt_in_at: datetime | None
    is_active: bool
```

Crie `backend/app/schemas/patient.py`:

```python
import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class PatientCreate(BaseModel):
    name: str = Field(min_length=1)
    species: str = Field(min_length=1)
    owner_id: uuid.UUID
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    species: str | None = None
    breed: str | None = None
    weight_kg: Decimal | None = Field(default=None, gt=0)
    notes: str | None = None
    is_active: bool | None = None


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    species: str
    breed: str | None
    weight_kg: Decimal | None
    notes: str | None
    is_active: bool
```

Crie `backend/app/schemas/kennel.py` no mesmo formato, com `KennelCreate` (`name`, `area`), `KennelUpdate` (`name`, `area`, `is_active`) e `KennelOut` (`id`, `name`, `area`, `is_active`).

- [ ] **Step 7: Implementar o router de owners**

Crie `backend/app/api/routes/owners.py`:

```python
import uuid
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Owner
from app.schemas.owner import OwnerCreate, OwnerOut, OwnerUpdate
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page, paginate
from app.services.audit import ActorInfo, AuditService

router = APIRouter(prefix="/api/v1/owners", tags=["owners"])


@router.get("", response_model=Page[OwnerOut])
async def list_owners(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
    include_inactive: bool = False,
) -> Page[OwnerOut]:
    stmt = sa.select(Owner).where(Owner.clinic_id == auth.clinic_id)
    if not include_inactive:
        stmt = stmt.where(Owner.is_active.is_(True))
    rows, next_cursor = await paginate(session, stmt, id_column=Owner.id, limit=limit, cursor=cursor)
    return Page[OwnerOut](items=[OwnerOut.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    return OwnerOut.model_validate(owner)


@router.post("", response_model=OwnerOut, status_code=201)
async def create_owner(
    payload: OwnerCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = Owner(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(owner)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="owner_created",
        entity_type="owner", entity_id=owner.id, after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)


@router.patch("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: uuid.UUID,
    payload: OwnerUpdate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OwnerOut:
    owner = await get_tenant_obj(session, Owner, owner_id, auth.clinic_id)
    before = AuditService.snapshot(owner)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(owner, field, value)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="owner_updated",
        entity_type="owner", entity_id=owner.id,
        before=before, after=AuditService.snapshot(owner),
    )
    await session.commit()
    return OwnerOut.model_validate(owner)
```

- [ ] **Step 8: Implementar os routers de kennels e patients**

`backend/app/api/routes/kennels.py` repete a estrutura acima trocando `Owner`→`Kennel` e as actions para `kennel_created`/`kennel_updated`.

`backend/app/api/routes/patients.py` faz o mesmo com uma diferença — a **regra transversal 2** na criação e a validação de `owner_id`:

```python
@router.post("", response_model=PatientOut, status_code=201)
async def create_patient(
    payload: PatientCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PatientOut:
    # FK vinda do body é validada contra o tenant: 404 (nunca 403 — não vazar existência).
    await get_tenant_obj(session, Owner, payload.owner_id, auth.clinic_id)
    patient = Patient(clinic_id=auth.clinic_id, **payload.model_dump())
    session.add(patient)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="patient_created",
        entity_type="patient", entity_id=patient.id, after=AuditService.snapshot(patient),
    )
    await session.commit()
    return PatientOut.model_validate(patient)
```

Registre os três routers em `create_app()` (`app.include_router(...)`).

- [ ] **Step 9: Gerar e revisar a migração 0004**

Run (em `backend/`): `uv run alembic revision --autogenerate -m "kennels owners patients"`
Confira que o arquivo gerado cria as três tabelas com os índices de `clinic_id` e a FK `patients.owner_id → owners.id`. Renomeie o arquivo para `0004_kennels_owners_patients.py` e ajuste `revision`/`down_revision` para `"0004"`/`"0003"`.

- [ ] **Step 10: Acrescentar as factories**

Em `backend/tests/factories.py`:

```python
async def make_kennel(session, *, clinic, **overrides):
    kennel = Kennel(clinic_id=clinic.id, **{"name": "Box 01", **overrides})
    session.add(kennel)
    await session.flush()
    return kennel


async def make_owner(session, *, clinic, **overrides):
    owner = Owner(
        clinic_id=clinic.id,
        **{"name": "Tutor Teste", "phone_e164": "+5511999990000", **overrides},
    )
    session.add(owner)
    await session.flush()
    return owner


async def make_patient(session, *, clinic, owner=None, **overrides):
    owner = owner or await make_owner(session, clinic=clinic)
    patient = Patient(
        clinic_id=clinic.id, owner_id=owner.id,
        **{"name": "Thor", "species": "dog", **overrides},
    )
    session.add(patient)
    await session.flush()
    return patient
```

- [ ] **Step 11: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_owners_patients_kennels.py -q && uv run pytest -q && uv run ruff check .`
Expected: `10 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 12: Commit**

```bash
git add backend/app/models backend/app/schemas backend/app/api/routes backend/alembic/versions/0004_kennels_owners_patients.py backend/app/main.py backend/tests
git commit -m "feat(cadastro): owners, patients e kennels paginados, auditados e isolados por tenant"
```

---

### Task 8: Hospitalizations (admissão com consentimento, limite suave de leitos e desfecho)

**Files:**
- Create: `backend/app/models/hospitalization.py`
- Modify: `backend/app/models/__init__.py` (reexportar `Hospitalization`, `HospitalizationStatus`, `ConsentStatus`)
- Create: `backend/app/schemas/hospitalization.py`
- Create: `backend/app/services/hospitalization.py`
- Create: `backend/app/api/routes/hospitalizations.py`
- Create: `backend/alembic/versions/0005_hospitalizations.py`
- Modify: `backend/app/main.py` (incluir o router)
- Modify: `backend/tests/factories.py` (acrescentar `make_hospitalization`)
- Test: `backend/tests/test_hospitalizations.py`

**Interfaces:**
- Consumes: `AppError` (Task 1), `Base` (Task 2), `Clinic`/`Membership` (Task 3), `AuditService` (Task 4), deps (Tasks 5–6), `Kennel`/`Owner`/`Patient` e `Page`/`paginate` (Task 7).
- Produces:
  - Model `Hospitalization` + enums `HospitalizationStatus` (`active|discharged|died|left_ama`) e `ConsentStatus` (`consent_recorded|emergency_no_consent`), com `UNIQUE (id, clinic_id)` e índice `(clinic_id, status)`.
  - `app.services.hospitalization.HospitalizationService.admit(session, *, clinic, payload, actor) -> tuple[Hospitalization, str | None]` — devolve a internação e um `warning` (`"bed_limit_exceeded"` ou `None`).
  - `app.services.hospitalization.HospitalizationService.close(session, *, hospitalization, outcome, note, actor) -> Hospitalization`.
  - `POST /api/v1/hospitalizations` → 201 `{"hospitalization": {...}, "warning": str | None}`.
  - `GET /api/v1/hospitalizations/{id}` → `{"hospitalization": {...}, "prescriptions": [], "tasks": []}` — **as duas listas nascem vazias aqui e são preenchidas pelas Tasks 9 (prescriptions) e 13 (tasks)**; o schema já reserva os campos para que o contrato não mude depois.
  - `POST /api/v1/hospitalizations/{id}/outcome` → 200; nota obrigatória em `died`/`left_ama`; `confirm_pending_tasks` já aceito no schema — **a contagem e o cancelamento de tarefas futuras entram na Task 12**, quando o model `Task` existir.
  - Factory `make_hospitalization(session, *, clinic, patient=None, membership=None, **overrides)`.
  - Migração `0005`.

> Decisão de produto (spec §1): o limite de leitos é **suave**. Estourar cria a internação assim mesmo e devolve `warning`, porque bloquear uma admissão por causa de plano seria indefensável num sistema de cuidado.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_hospitalizations.py`:

```python
import sqlalchemy as sa

from app.models import AuditEntry, Hospitalization
from tests.factories import (
    make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user,
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


async def test_admitir_paciente(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    kennel = await make_kennel(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "kennel_id": str(kennel.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "consent_recorded",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["warning"] is None
    assert body["hospitalization"]["status"] == "active"
    assert body["hospitalization"]["admitted_at"] is not None


async def test_emergencia_sem_termo_exige_motivo(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
        },
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "consent_reason_required"

    ok = await client.post(
        "/api/v1/hospitalizations",
        json={
            "patient_id": str(patient.id),
            "vet_membership_id": str(membership.id),
            "consent_status": "emergency_no_consent",
            "consent_reason": "Paciente chegou em parada; tutor a caminho.",
        },
        headers=bearer(personal_token(membership)),
    )
    assert ok.status_code == 201


async def test_limite_de_leitos_e_suave(client, session):
    clinic, membership = await _vet(session)
    clinic.bed_limit = 1
    await session.flush()
    token = bearer(personal_token(membership))

    primeiro = await make_patient(session, clinic=clinic, name="Thor")
    segundo = await make_patient(session, clinic=clinic, name="Nina")

    r1 = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(primeiro.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )
    assert r1.json()["warning"] is None

    r2 = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(segundo.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )
    # Cria assim mesmo: cuidado nunca é bloqueado por plano.
    assert r2.status_code == 201
    assert r2.json()["warning"] == "bed_limit_exceeded"

    entry = (
        await session.execute(
            sa.select(AuditEntry).where(AuditEntry.action == "bed_limit_exceeded")
        )
    ).scalars().first()
    assert entry is not None


async def test_desfecho_alta(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    resp = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={"outcome": "discharged"}, headers=token,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "discharged"
    assert resp.json()["ended_at"] is not None


async def test_obito_e_retirada_exigem_nota(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    for outcome in ("died", "left_ama"):
        resp = await client.post(
            f"/api/v1/hospitalizations/{created['id']}/outcome",
            json={"outcome": outcome}, headers=token,
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "outcome_note_required"

    ok = await client.post(
        f"/api/v1/hospitalizations/{created['id']}/outcome",
        json={"outcome": "died", "note": "Parada cardiorrespiratória às 03:12."},
        headers=token,
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "died"


async def test_ficha_reserva_prescriptions_e_tasks(client, session):
    clinic, membership = await _vet(session)
    patient = await make_patient(session, clinic=clinic)
    token = bearer(personal_token(membership))
    created = (await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "vet_membership_id": str(membership.id),
              "consent_status": "consent_recorded"},
        headers=token,
    )).json()["hospitalization"]

    resp = await client.get(f"/api/v1/hospitalizations/{created['id']}", headers=token)
    assert resp.status_code == 200
    # Contrato reservado: Task 9 preenche prescriptions, Task 13 preenche tasks.
    assert resp.json()["prescriptions"] == []
    assert resp.json()["tasks"] == []


async def test_isolamento_de_tenant_em_fk_de_body(client, session):
    clinic_a, membership_a = await _vet(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    patient_b = await make_patient(session, clinic=clinic_b)

    resp = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient_b.id), "vet_membership_id": str(membership_a.id),
              "consent_status": "consent_recorded"},
        headers=bearer(personal_token(membership_a)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_hospitalizations.py -q`
Expected: `ImportError: cannot import name 'Hospitalization' from 'app.models'`.

- [ ] **Step 3: Implementar o model**

Crie `backend/app/models/hospitalization.py`:

```python
import uuid
from datetime import UTC, datetime
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class HospitalizationStatus(StrEnum):
    active = "active"
    discharged = "discharged"
    died = "died"
    left_ama = "left_ama"


class ConsentStatus(StrEnum):
    consent_recorded = "consent_recorded"
    emergency_no_consent = "emergency_no_consent"


def _enum(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls, name=name, native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Hospitalization(Base):
    __tablename__ = "hospitalizations"
    __table_args__ = (
        # Barreira de tenancy no banco: filhos apontam para (id, clinic_id).
        sa.UniqueConstraint("id", "clinic_id", name="uq_hospitalizations_id_clinic"),
        sa.Index("ix_hospitalizations_clinic_status", "clinic_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    clinic_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("clinics.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("patients.id"))
    kennel_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("kennels.id"), default=None)
    vet_membership_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("memberships.id"))
    status: Mapped[HospitalizationStatus] = mapped_column(
        _enum(HospitalizationStatus, "hospitalization_status"),
        default=HospitalizationStatus.active,
    )
    admitted_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True), default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[datetime | None] = mapped_column(sa.TIMESTAMP(timezone=True), default=None)
    outcome_note: Mapped[str | None] = mapped_column(sa.Text, default=None)
    consent_status: Mapped[ConsentStatus] = mapped_column(_enum(ConsentStatus, "consent_status"))
    consent_reason: Mapped[str | None] = mapped_column(sa.Text, default=None)
```

- [ ] **Step 4: Implementar os schemas**

Crie `backend/app/schemas/hospitalization.py`:

```python
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class HospitalizationCreate(BaseModel):
    patient_id: uuid.UUID
    vet_membership_id: uuid.UUID
    kennel_id: uuid.UUID | None = None
    consent_status: Literal["consent_recorded", "emergency_no_consent"]
    consent_reason: str | None = None


class HospitalizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    kennel_id: uuid.UUID | None
    vet_membership_id: uuid.UUID
    status: str
    admitted_at: datetime
    ended_at: datetime | None
    outcome_note: str | None
    consent_status: str
    consent_reason: str | None


class HospitalizationCreated(BaseModel):
    hospitalization: HospitalizationOut
    warning: str | None = None


class HospitalizationDetail(BaseModel):
    hospitalization: HospitalizationOut
    # Reservados aqui para o contrato não mudar: Task 9 preenche prescriptions,
    # Task 13 preenche tasks.
    prescriptions: list[Any] = []
    tasks: list[Any] = []


class OutcomeRequest(BaseModel):
    outcome: Literal["discharged", "died", "left_ama"]
    note: str | None = None
    confirm_pending_tasks: bool = False
```

- [ ] **Step 5: Implementar o serviço**

Crie `backend/app/services/hospitalization.py`:

```python
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import Clinic, Hospitalization, HospitalizationStatus
from app.schemas.hospitalization import HospitalizationCreate
from app.services.audit import ActorInfo, AuditService

OUTCOMES_REQUIRING_NOTE = {"died", "left_ama"}


class HospitalizationService:
    @staticmethod
    async def admit(
        session: AsyncSession, *, clinic: Clinic, payload: HospitalizationCreate, actor: ActorInfo
    ) -> tuple[Hospitalization, str | None]:
        if payload.consent_status == "emergency_no_consent" and not payload.consent_reason:
            raise AppError("consent_reason_required", 422)

        hospitalization = Hospitalization(clinic_id=clinic.id, **payload.model_dump())
        session.add(hospitalization)
        await session.flush()
        await AuditService.record(
            session, clinic_id=clinic.id, actor=actor, action="hospitalization_admitted",
            entity_type="hospitalization", entity_id=hospitalization.id,
            after=AuditService.snapshot(hospitalization),
        )

        warning = None
        if clinic.bed_limit is not None:
            active = await session.scalar(
                sa.select(sa.func.count())
                .select_from(Hospitalization)
                .where(
                    Hospitalization.clinic_id == clinic.id,
                    Hospitalization.status == HospitalizationStatus.active,
                )
            )
            if active > clinic.bed_limit:
                warning = "bed_limit_exceeded"
                await AuditService.record(
                    session, clinic_id=clinic.id, actor=actor, action="bed_limit_exceeded",
                    entity_type="clinic", entity_id=clinic.id,
                    extra={"active": active, "bed_limit": clinic.bed_limit},
                )
        return hospitalization, warning

    @staticmethod
    async def close(
        session: AsyncSession, *, hospitalization: Hospitalization, outcome: str,
        note: str | None, actor: ActorInfo,
    ) -> Hospitalization:
        if outcome in OUTCOMES_REQUIRING_NOTE and not note:
            raise AppError("outcome_note_required", 422)

        before = AuditService.snapshot(hospitalization)
        hospitalization.status = HospitalizationStatus(outcome)
        hospitalization.ended_at = datetime.now(UTC)
        hospitalization.outcome_note = note
        await session.flush()
        await AuditService.record(
            session, clinic_id=hospitalization.clinic_id, actor=actor,
            action="hospitalization_closed", entity_type="hospitalization",
            entity_id=hospitalization.id, before=before,
            after=AuditService.snapshot(hospitalization),
        )
        return hospitalization
```

- [ ] **Step 6: Implementar o router**

Crie `backend/app/api/routes/hospitalizations.py`:

```python
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_operator, get_session, get_tenant_obj
from app.models import Clinic, Hospitalization, Kennel, Membership, Patient
from app.schemas.hospitalization import (
    HospitalizationCreate, HospitalizationCreated, HospitalizationDetail,
    HospitalizationOut, OutcomeRequest,
)
from app.services.audit import ActorInfo, AuditService
from app.services.hospitalization import HospitalizationService

router = APIRouter(prefix="/api/v1/hospitalizations", tags=["hospitalizations"])


@router.post("", response_model=HospitalizationCreated, status_code=201)
async def admit(
    payload: HospitalizationCreate,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationCreated:
    # Regra transversal 2: toda FK de body é validada contra o tenant.
    await get_tenant_obj(session, Patient, payload.patient_id, auth.clinic_id)
    await get_tenant_obj(session, Membership, payload.vet_membership_id, auth.clinic_id)
    if payload.kennel_id is not None:
        await get_tenant_obj(session, Kennel, payload.kennel_id, auth.clinic_id)

    clinic = await session.get(Clinic, auth.clinic_id)
    hospitalization, warning = await HospitalizationService.admit(
        session, clinic=clinic, payload=payload, actor=actor
    )
    await session.commit()
    return HospitalizationCreated(
        hospitalization=HospitalizationOut.model_validate(hospitalization), warning=warning
    )


@router.get("/{hospitalization_id}", response_model=HospitalizationDetail)
async def detail(
    hospitalization_id: uuid.UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationDetail:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    return HospitalizationDetail(
        hospitalization=HospitalizationOut.model_validate(hospitalization)
    )


@router.post("/{hospitalization_id}/outcome", response_model=HospitalizationOut)
async def close(
    hospitalization_id: uuid.UUID,
    payload: OutcomeRequest,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> HospitalizationOut:
    hospitalization = await get_tenant_obj(
        session, Hospitalization, hospitalization_id, auth.clinic_id
    )
    await HospitalizationService.close(
        session, hospitalization=hospitalization, outcome=payload.outcome,
        note=payload.note, actor=actor,
    )
    await session.commit()
    return HospitalizationOut.model_validate(hospitalization)
```

Registre o router em `create_app()`.

- [ ] **Step 7: Gerar a migração 0005**

Run: `uv run alembic revision --autogenerate -m "hospitalizations"`
Confira `UniqueConstraint("id", "clinic_id")` e `Index("ix_hospitalizations_clinic_status")` no arquivo gerado; renomeie para `0005_hospitalizations.py` com `revision="0005"`, `down_revision="0004"`.

- [ ] **Step 8: Acrescentar a factory**

Em `backend/tests/factories.py`:

```python
async def make_hospitalization(session, *, clinic, patient=None, membership=None, **overrides):
    patient = patient or await make_patient(session, clinic=clinic)
    if membership is None:
        user = await make_user(session)
        membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hospitalization = Hospitalization(
        clinic_id=clinic.id, patient_id=patient.id, vet_membership_id=membership.id,
        **{"consent_status": "consent_recorded", **overrides},
    )
    session.add(hospitalization)
    await session.flush()
    return hospitalization
```

- [ ] **Step 9: Rodar e ver passar**

Run (em `backend/`): `uv run pytest tests/test_hospitalizations.py -q && uv run pytest -q && uv run ruff check .`
Expected: `7 passed` no arquivo, suíte inteira verde, lint limpo.

- [ ] **Step 10: Commit**

```bash
git add backend/app/models/hospitalization.py backend/app/models/__init__.py backend/app/schemas/hospitalization.py backend/app/services/hospitalization.py backend/app/api/routes/hospitalizations.py backend/alembic/versions/0005_hospitalizations.py backend/app/main.py backend/tests
git commit -m "feat(internacao): admissao com consentimento, limite suave de leitos e desfecho"
```

---
