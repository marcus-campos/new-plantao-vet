### Task 13: Fila de tarefas por janela e o estado exibido

**Files:**
- Modify: `backend/app/services/tasks.py` (acrescentar `display_state`)
- Create: `backend/app/schemas/task.py`
- Create: `backend/app/api/routes/tasks.py`
- Modify: `backend/app/api/routes/hospitalizations.py` (preencher `tasks` no detalhe)
- Modify: `backend/app/main.py` (incluir o router)
- Test: `backend/tests/test_task_queue.py`

**Interfaces:**
- Consumes: `Task`/`TaskStatus` (Task 11), `Clinic.timezone` (Task 3), deps (Tasks 5–6).
- Produces:
  - `TaskService.display_state(task: Task, now: datetime) -> str` — assinatura EXATA do brief; devolve `on_time | due | overdue` para `pending`, ou o próprio status.
  - `TaskService.default_window(clinic, now) -> tuple[datetime, datetime]` — de `now` a `now + 12h`.
  - `app.schemas.task.TaskOut` — inclui `display_state` calculado.
  - `GET /api/v1/tasks?from=&to=&limit=` → `Page[TaskOut]` ordenado por `scheduled_for`.
  - `GET /api/v1/hospitalizations/{id}` agora traz `tasks` da janela.

> Achado clínico 9 (spec §9): `due=today` esconderia as tarefas de 02h e 04h do plantonista das 22h — justamente as âncoras da madrugada. Por isso a janela é explícita, e o default (`agora → +12h`) cobre um turno inteiro.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_task_queue.py`:

```python
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Task
from app.services.tasks import TaskService
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token

SP = ZoneInfo("America/Sao_Paulo")


def _task(**overrides) -> Task:
    values = {
        "title": "Dipirona", "category": "medication", "criticality": "normal",
        "tolerance_minutes": 60, "status": "pending",
        "scheduled_for": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return Task(**values)


def test_display_state_antes_do_horario():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 11, 59, tzinfo=UTC)) == "on_time"


def test_display_state_exatamente_no_horario():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 0, tzinfo=UTC)) == "due"


def test_display_state_no_limite_da_tolerancia():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 13, 0, tzinfo=UTC)) == "due"


def test_display_state_um_minuto_depois_da_tolerancia():
    task = _task()
    assert TaskService.display_state(task, datetime(2026, 9, 1, 13, 1, tzinfo=UTC)) == "overdue"


def test_display_state_critica_tem_janela_menor():
    task = _task(criticality="critical", tolerance_minutes=30)
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 30, tzinfo=UTC)) == "due"
    assert TaskService.display_state(task, datetime(2026, 9, 1, 12, 31, tzinfo=UTC)) == "overdue"


def test_display_state_de_status_finalizado_e_o_proprio_status():
    agora = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    for status in ("done", "partial", "not_done", "cancelled"):
        assert TaskService.display_state(_task(status=status), agora) == status


async def test_fila_por_janela_explicita(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    inicio = datetime.now(UTC)
    prescription = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, starts_at=inicio, frequency_minutes=60
    )
    from app.services.tasks import TaskService as TS
    await TS.materialize(
        session, prescription=prescription, clinic=clinic, until=inicio + timedelta(hours=24)
    )
    await session.flush()

    resp = await client.get(
        f"/api/v1/tasks?from={inicio.isoformat()}&to={(inicio + timedelta(hours=3)).isoformat()}",
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    itens = resp.json()["items"]
    assert 3 <= len(itens) <= 4
    assert itens == sorted(itens, key=lambda item: item["scheduled_for"])
    assert itens[0]["display_state"] in ("on_time", "due")


async def test_plantao_noturno_ve_as_tarefas_da_madrugada(client, session):
    """Às 22h a janela default (12h) precisa alcançar as âncoras de 02h e 04h."""
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)

    agora_local = datetime.now(SP).replace(hour=22, minute=0, second=0, microsecond=0)
    agora = agora_local.astimezone(UTC)
    for hora in (2, 4):
        alvo = (agora_local + timedelta(days=1)).replace(hour=hora, minute=0)
        session.add(Task(
            clinic_id=clinic.id, hospitalization_id=hosp.id, title=f"Madrugada {hora}h",
            category="monitoring", scheduled_for=alvo.astimezone(UTC),
            criticality="normal", tolerance_minutes=60, status="pending",
        ))
    await session.flush()

    resp = await client.get(
        f"/api/v1/tasks?from={agora.isoformat()}&to={(agora + timedelta(hours=12)).isoformat()}",
        headers=bearer(personal_token(membership)),
    )
    titulos = [item["title"] for item in resp.json()["items"]]
    assert "Madrugada 2h" in titulos
    assert "Madrugada 4h" in titulos


async def test_janela_default_sao_12_horas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    hosp = await make_hospitalization(session, clinic=clinic)
    agora = datetime.now(UTC)
    session.add(Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Dentro",
        category="care", scheduled_for=agora + timedelta(hours=6),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    session.add(Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Fora",
        category="care", scheduled_for=agora + timedelta(hours=30),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    await session.flush()

    itens = (await client.get("/api/v1/tasks", headers=bearer(personal_token(membership)))).json()["items"]
    titulos = [item["title"] for item in itens]
    assert "Dentro" in titulos
    assert "Fora" not in titulos


async def test_detalhe_da_internacao_traz_tarefas(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    token = bearer(personal_token(membership))
    await client.post(
        f"/api/v1/hospitalizations/{hosp.id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona",
              "frequency_minutes": 480, "criticality": "normal", "details": {}},
        headers=token,
    )
    detail = (await client.get(f"/api/v1/hospitalizations/{hosp.id}", headers=token)).json()
    assert len(detail["tasks"]) > 0
    assert "display_state" in detail["tasks"][0]


async def test_isolamento_de_tenant_na_fila(client, session):
    clinic_a = await make_clinic(session, slug="a")
    clinic_b = await make_clinic(session, slug="b")
    user = await make_user(session)
    membership_a = await make_membership(session, clinic=clinic_a, user=user, role="tech")
    hosp_b = await make_hospitalization(session, clinic=clinic_b)
    session.add(Task(
        clinic_id=clinic_b.id, hospitalization_id=hosp_b.id, title="Da clínica B",
        category="care", scheduled_for=datetime.now(UTC) + timedelta(hours=1),
        criticality="normal", tolerance_minutes=60, status="pending",
    ))
    await session.flush()

    itens = (await client.get("/api/v1/tasks", headers=bearer(personal_token(membership_a)))).json()["items"]
    assert itens == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_queue.py -q`
Expected: `AttributeError: type object 'TaskService' has no attribute 'display_state'`.

- [ ] **Step 3: Implementar display_state e a janela default**

Acrescente a `backend/app/services/tasks.py`:

```python
    @staticmethod
    def display_state(task: Task, now: datetime) -> str:
        """'Atrasada' NUNCA é persistida (spec §2): é derivada na leitura, para
        que board e ficha jamais divirjam — o bug fatal do concorrente."""
        if task.status != TaskStatus.pending:
            return str(task.status)
        if now < task.scheduled_for:
            return "on_time"
        if now <= task.scheduled_for + timedelta(minutes=task.tolerance_minutes):
            return "due"
        return "overdue"

    @staticmethod
    def default_window(clinic: Clinic, now: datetime) -> tuple[datetime, datetime]:
        """Um turno inteiro à frente: às 22h o plantonista precisa enxergar 02h e 04h."""
        return now, now + timedelta(hours=12)
```

- [ ] **Step 4: Implementar o schema e o router**

Crie `backend/app/schemas/task.py`:

```python
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    hospitalization_id: uuid.UUID
    prescription_id: uuid.UUID | None
    title: str
    category: str
    scheduled_for: datetime
    criticality: str
    tolerance_minutes: int
    status: str
    display_state: str
    executed_at: datetime | None
    executed_by: uuid.UUID | None
    retroactive: bool
    early: bool
    outcome_reason: str | None
    values: dict[str, Any] | None
    price_minor: int | None

    @classmethod
    def from_task(cls, task: Any, now: datetime) -> "TaskOut":
        from app.services.tasks import TaskService

        data = {field: getattr(task, field) for field in cls.model_fields if field != "display_state"}
        return cls(**data, display_state=TaskService.display_state(task, now))
```

Crie `backend/app/api/routes/tasks.py`:

```python
from datetime import UTC, datetime
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.models import Clinic, Task
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page
from app.schemas.task import TaskOut
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("", response_model=Page[TaskOut])
async def list_tasks(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> Page[TaskOut]:
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    default_from, default_to = TaskService.default_window(clinic, now)
    window_start = from_ or default_from
    window_end = to or default_to

    rows = list((await session.execute(
        sa.select(Task)
        .where(
            Task.clinic_id == auth.clinic_id,
            Task.scheduled_for >= window_start,
            Task.scheduled_for <= window_end,
            Task.status != "cancelled",
        )
        .order_by(Task.scheduled_for.asc())
        .limit(limit)
    )).scalars())
    return Page[TaskOut](items=[TaskOut.from_task(row, now) for row in rows], next_cursor=None)
```

Registre o router em `create_app()`.

- [ ] **Step 5: Preencher `tasks` no detalhe da internação**

Em `backend/app/api/routes/hospitalizations.py`, dentro de `detail`:

```python
    now = datetime.now(UTC)
    clinic = await session.get(Clinic, auth.clinic_id)
    window_start, window_end = TaskService.default_window(clinic, now)
    tasks = list((await session.execute(
        sa.select(Task)
        .where(
            Task.hospitalization_id == hospitalization.id,
            Task.scheduled_for >= window_start - timedelta(hours=12),
            Task.scheduled_for <= window_end,
            Task.status != "cancelled",
        )
        .order_by(Task.scheduled_for)
    )).scalars())
```

e passe `tasks=[TaskOut.from_task(row, now) for row in tasks]` ao `HospitalizationDetail`.

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_task_queue.py -q && uv run pytest -q && uv run ruff check .`
Expected: `11 passed` no arquivo, suíte verde, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/tasks.py backend/app/schemas/task.py backend/app/api/routes backend/app/main.py backend/tests/test_task_queue.py
git commit -m "feat(tarefas): fila por janela explicita e estado derivado na leitura"
```

---

### Task 14: Execução de tarefas — transição atômica, janela nos dois lados e guardrails PRN

**Files:**
- Modify: `backend/app/services/tasks.py` (`execute`, `mark_not_done`, `check_prn_guardrails`)
- Modify: `backend/app/schemas/task.py` (`TaskExecute`, `TaskNotDone`, `TaskAdHoc`)
- Modify: `backend/app/api/routes/tasks.py` (três rotas)
- Test: `backend/tests/test_task_execution.py`

**Interfaces:**
- Consumes: `Task`/`TaskStatus` (Task 11), `Prescription` (Task 9), `get_operator`/`ActorInfo` (Tasks 5–6), `AuditService` (Task 4), `AppError` (Task 1).
- Produces:
  - `POST /api/v1/tasks/{id}/execute` · `POST /api/v1/tasks/{id}/not-done` · `POST /api/v1/tasks/ad-hoc`.
  - `TaskService.transition(session, *, task_id, clinic_id, values) -> Task` — o UPDATE condicional atômico; `None` → `AppError("task_already_processed", 409)`.
  - `TaskService.check_prn_guardrails(session, *, prescription, now) -> dict | None` — devolve o motivo da violação ou `None`.

> É a task mais crítica do plano. Achado de engenharia 1 (spec §9): sem transição atômica, painel e app podem baixar a mesma dose e o sistema registra as duas sem piar — risco de dose dupla. Achado clínico 8: a janela ISMP vale **nos dois lados**; executar cedo demais é erro de medicação tanto quanto atrasar.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_task_execution.py`:

```python
import asyncio
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.models import AuditEntry, Task
from tests.factories import (
    make_clinic, make_hospitalization, make_membership, make_prescription, make_user,
)
from tests.helpers import bearer, personal_token


async def _cenario(session, *, scheduled_for=None, tolerance_minutes=60, criticality="normal"):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(
        session, clinic=clinic, user=user, role="tech",
        license_number="9876", license_authority="CRMV-SP",
    )
    hosp = await make_hospitalization(session, clinic=clinic)
    task = Task(
        clinic_id=clinic.id, hospitalization_id=hosp.id, title="Dipirona",
        category="medication", scheduled_for=scheduled_for or datetime.now(UTC),
        criticality=criticality, tolerance_minutes=tolerance_minutes, status="pending",
        price_minor=1800,
    )
    session.add(task)
    await session.flush()
    return clinic, membership, hosp, task


async def test_executar_registra_autor_e_registro_profissional(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"values": {"note": "sem intercorrência"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"
    assert resp.json()["executed_at"] is not None

    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    assert entry.actor_license == "9876"
    assert entry.actor_license_authority == "CRMV-SP"


async def test_execucao_concorrente_uma_ganha_outra_recebe_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    await session.commit()
    headers = bearer(personal_token(membership))

    primeira, segunda = await asyncio.gather(
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
        client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers),
    )
    codigos = sorted([primeira.status_code, segunda.status_code])
    assert codigos == [200, 409]
    perdedora = primeira if primeira.status_code == 409 else segunda
    assert perdedora.json()["error"]["code"] == "task_already_processed"


async def test_executar_tarefa_ja_finalizada_e_409(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    de_novo = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert de_novo.status_code == 409


async def test_retroativo_exige_hora_real_do_procedimento(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"retroactive": True}, headers=headers
    )
    assert sem_hora.status_code == 422

    realizada_em = datetime.now(UTC) - timedelta(minutes=40)
    com_hora = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"retroactive": True, "performed_at": realizada_em.isoformat()}, headers=headers,
    )
    assert com_hora.status_code == 200
    assert com_hora.json()["retroactive"] is True

    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    # Os DOIS instantes ficam registrados: quando foi feito e quando foi apontado.
    assert entry.payload["extra"]["performed_at"] is not None
    assert entry.payload["extra"]["recorded_at"] is not None


async def test_execucao_precoce_exige_confirmacao(client, session):
    daqui_a_muito = datetime.now(UTC) + timedelta(hours=5)
    clinic, membership, hosp, task = await _cenario(session, scheduled_for=daqui_a_muito)
    headers = bearer(personal_token(membership))

    sem_confirmar = await client.post(f"/api/v1/tasks/{task.id}/execute", json={}, headers=headers)
    assert sem_confirmar.status_code == 409
    assert sem_confirmar.json()["error"]["code"] == "early_confirmation_required"

    confirmando = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"confirm_early": True}, headers=headers
    )
    assert confirmando.status_code == 200
    assert confirmando.json()["early"] is True


async def test_parcial_exige_dose_administrada(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))

    sem_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={"partial": True}, headers=headers
    )
    assert sem_dose.status_code == 422

    com_dose = await client.post(
        f"/api/v1/tasks/{task.id}/execute",
        json={"partial": True, "values": {"dose_given": "metade"}}, headers=headers,
    )
    assert com_dose.status_code == 200
    assert com_dose.json()["status"] == "partial"


async def test_nao_realizada_com_motivo(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "fasting"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_done"
    assert resp.json()["outcome_reason"] == "fasting"


async def test_motivo_outro_exige_detalhe(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    headers = bearer(personal_token(membership))
    sem_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done", json={"reason": "other"}, headers=headers
    )
    assert sem_detalhe.status_code == 422

    com_detalhe = await client.post(
        f"/api/v1/tasks/{task.id}/not-done",
        json={"reason": "other", "values": {"outcome_detail": "acesso venoso perdido"}},
        headers=headers,
    )
    assert com_detalhe.status_code == 200


async def test_prn_respeita_intervalo_minimo_com_override(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, kind="prn", frequency_minutes=None,
        name="Metadona", max_doses_24h=4, min_interval_minutes=240,
    )
    headers = bearer(personal_token(membership))

    primeira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert primeira.status_code == 201

    cedo_demais = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert cedo_demais.status_code == 409
    assert cedo_demais.json()["error"]["code"] == "prn_guardrail"
    assert cedo_demais.json()["error"]["params"]["rule"] == "min_interval_minutes"

    # Aviso, nunca bloqueio duro: o vet decide e o override fica auditado.
    com_override = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id), "override": True},
        headers=headers,
    )
    assert com_override.status_code == 201
    entry = (await session.execute(
        sa.select(AuditEntry).where(AuditEntry.action == "task_executed").order_by(AuditEntry.id.desc())
    )).scalars().first()
    assert entry.payload["extra"]["override"] is True


async def test_prn_respeita_maximo_em_24h(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    hosp = await make_hospitalization(session, clinic=clinic, membership=membership)
    prn = await make_prescription(
        session, clinic=clinic, hospitalization=hosp, kind="prn", frequency_minutes=None,
        name="Metadona", max_doses_24h=2, min_interval_minutes=None,
    )
    headers = bearer(personal_token(membership))

    for _ in range(2):
        assert (await client.post(
            "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
        )).status_code == 201

    terceira = await client.post(
        "/api/v1/tasks/ad-hoc", json={"prescription_id": str(prn.id)}, headers=headers
    )
    assert terceira.status_code == 409
    assert terceira.json()["error"]["params"]["rule"] == "max_doses_24h"


async def test_evento_avulso_com_titulo_livre(client, session):
    clinic, membership, hosp, task = await _cenario(session)
    resp = await client.post(
        "/api/v1/tasks/ad-hoc",
        json={"hospitalization_id": str(hosp.id), "title": "Episódio de vômito",
              "category": "care", "values": {"note": "bilioso, pequeno volume"}},
        headers=bearer(personal_token(membership)),
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "done"
    assert resp.json()["title"] == "Episódio de vômito"


async def test_modo_estacao_sem_operator_token_e_403(client, session):
    from tests.helpers import station_token

    clinic, membership, hosp, task = await _cenario(session)
    clinic.station_key_hash = None
    resp = await client.post(
        f"/api/v1/tasks/{task.id}/execute", json={},
        headers=bearer(station_token(clinic)),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "operator_required"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_task_execution.py -q`
Expected: `404` nas três rotas.

- [ ] **Step 3: Implementar a transição atômica**

Acrescente a `backend/app/services/tasks.py`:

```python
    @staticmethod
    async def transition(
        session: AsyncSession, *, task_id: uuid.UUID, clinic_id: uuid.UUID, values: dict
    ) -> Task:
        """Transição ATÔMICA (brief): um único UPDATE condicional. Se outra pessoa
        baixou a tarefa primeiro, zero linhas voltam e o segundo recebe 409 — sem
        isso, painel e app registrariam a mesma dose duas vezes."""
        stmt = (
            sa.update(Task)
            .where(Task.id == task_id, Task.clinic_id == clinic_id, Task.status == "pending")
            .values(**values)
            .returning(Task)
        )
        task = (await session.execute(stmt)).scalar_one_or_none()
        if task is None:
            raise AppError("task_already_processed", 409)
        return task

    @staticmethod
    async def check_prn_guardrails(
        session: AsyncSession, *, prescription: Prescription, now: datetime
    ) -> dict | None:
        """Aviso, nunca bloqueio duro (pesquisa §4: fricção gera workaround que
        falsifica o registro). Quem decide é o profissional; o sistema audita."""
        if prescription.min_interval_minutes:
            ultima = await session.scalar(
                sa.select(sa.func.max(Task.executed_at)).where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                )
            )
            if ultima is not None:
                minutos = (now - ultima).total_seconds() / 60
                if minutos < prescription.min_interval_minutes:
                    return {
                        "rule": "min_interval_minutes",
                        "required_minutes": prescription.min_interval_minutes,
                        "elapsed_minutes": int(minutos),
                    }
        if prescription.max_doses_24h:
            doses = await session.scalar(
                sa.select(sa.func.count()).select_from(Task).where(
                    Task.prescription_id == prescription.id,
                    Task.status.in_(["done", "partial"]),
                    Task.executed_at >= now - timedelta(hours=24),
                )
            )
            if doses >= prescription.max_doses_24h:
                return {
                    "rule": "max_doses_24h",
                    "max": prescription.max_doses_24h,
                    "given": doses,
                }
        return None
```

- [ ] **Step 4: Implementar os schemas de execução**

Acrescente a `backend/app/schemas/task.py`:

```python
class TaskExecute(BaseModel):
    values: dict[str, Any] | None = None
    retroactive: bool = False
    performed_at: datetime | None = None
    partial: bool = False
    confirm_early: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskExecute":
        if self.retroactive and self.performed_at is None:
            raise ValueError("performed_at é obrigatório quando retroactive=true")
        if self.partial and not (self.values or {}).get("dose_given"):
            raise ValueError("execução parcial exige values.dose_given")
        return self


class TaskNotDone(BaseModel):
    reason: Literal["refused", "fasting", "unavailable", "vet_order", "other"]
    values: dict[str, Any] | None = None

    @model_validator(mode="after")
    def check(self) -> "TaskNotDone":
        if self.reason == "other" and not (self.values or {}).get("outcome_detail"):
            raise ValueError("motivo 'other' exige values.outcome_detail")
        return self


class TaskAdHoc(BaseModel):
    prescription_id: uuid.UUID | None = None
    hospitalization_id: uuid.UUID | None = None
    title: str | None = None
    category: str = "care"
    values: dict[str, Any] | None = None
    override: bool = False

    @model_validator(mode="after")
    def check(self) -> "TaskAdHoc":
        if self.prescription_id is None and not (self.hospitalization_id and self.title):
            raise ValueError("informe prescription_id (PRN) ou hospitalization_id + title")
        return self
```

- [ ] **Step 5: Implementar as três rotas**

Acrescente a `backend/app/api/routes/tasks.py`:

```python
@router.post("/{task_id}/execute", response_model=TaskOut)
async def execute(
    task_id: uuid.UUID,
    payload: TaskExecute,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    current = await get_tenant_obj(session, Task, task_id, auth.clinic_id)

    # A janela ISMP vale nos DOIS lados: adiantar dose é erro como atrasar.
    early = now < current.scheduled_for - timedelta(minutes=current.tolerance_minutes)
    if early and not payload.confirm_early:
        raise AppError("early_confirmation_required", 409,
                       scheduled_for=current.scheduled_for.isoformat())

    executed_at = payload.performed_at or now
    task = await TaskService.transition(
        session, task_id=task_id, clinic_id=auth.clinic_id,
        values={
            "status": "partial" if payload.partial else "done",
            "executed_at": executed_at,
            "executed_by": actor.membership_id,
            "retroactive": payload.retroactive,
            "early": early,
            "values": payload.values,
        },
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_executed",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={
            "performed_at": executed_at.isoformat(),
            "recorded_at": now.isoformat(),
            "early": early,
        },
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/{task_id}/not-done", response_model=TaskOut)
async def not_done(
    task_id: uuid.UUID,
    payload: TaskNotDone,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    await get_tenant_obj(session, Task, task_id, auth.clinic_id)
    task = await TaskService.transition(
        session, task_id=task_id, clinic_id=auth.clinic_id,
        values={
            "status": "not_done", "executed_at": now, "executed_by": actor.membership_id,
            "outcome_reason": payload.reason, "values": payload.values,
        },
    )
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_not_done",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={"reason": payload.reason},
    )
    await session.commit()
    return TaskOut.from_task(task, now)


@router.post("/ad-hoc", response_model=TaskOut, status_code=201)
async def ad_hoc(
    payload: TaskAdHoc,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    actor: Annotated[ActorInfo, Depends(get_operator)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TaskOut:
    now = datetime.now(UTC)
    guardrail = None
    if payload.prescription_id is not None:
        prescription = await get_tenant_obj(
            session, Prescription, payload.prescription_id, auth.clinic_id
        )
        if prescription.kind != "prn":
            raise AppError("validation_error", 422, field="prescription_id")
        guardrail = await TaskService.check_prn_guardrails(
            session, prescription=prescription, now=now
        )
        if guardrail is not None and not payload.override:
            raise AppError("prn_guardrail", 409, **guardrail)
        task = Task(
            clinic_id=auth.clinic_id, hospitalization_id=prescription.hospitalization_id,
            prescription_id=prescription.id, title=prescription.name,
            category=prescription.category, price_minor=prescription.price_minor,
            criticality=prescription.criticality,
            tolerance_minutes=prescription.tolerance_minutes,
        )
    else:
        await get_tenant_obj(session, Hospitalization, payload.hospitalization_id, auth.clinic_id)
        task = Task(
            clinic_id=auth.clinic_id, hospitalization_id=payload.hospitalization_id,
            title=payload.title, category=payload.category,
            criticality="normal", tolerance_minutes=60,
        )

    task.scheduled_for = now
    task.status = "done"
    task.executed_at = now
    task.executed_by = actor.membership_id
    task.values = payload.values
    session.add(task)
    await session.flush()
    await AuditService.record(
        session, clinic_id=auth.clinic_id, actor=actor, action="task_executed",
        entity_type="task", entity_id=task.id, after=AuditService.snapshot(task),
        extra={
            "ad_hoc": True,
            "performed_at": now.isoformat(),
            "recorded_at": now.isoformat(),
            "override": payload.override and guardrail is not None,
            "guardrail": guardrail,
        },
    )
    await session.commit()
    return TaskOut.from_task(task, now)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `uv run pytest tests/test_task_execution.py -q`
Expected: `12 passed`.

> Se `test_execucao_concorrente_uma_ganha_outra_recebe_409` for instável, a causa é o harness compartilhar uma transação entre as duas chamadas: rode esse teste com um `client` que abra sessões independentes (fixture `client_isolated` com engine própria) em vez de relaxar a asserção — a garantia atômica é o ponto da task.

- [ ] **Step 7: Suíte, lint e commit**

```bash
uv run pytest -q && uv run ruff check .
git add backend/app/services/tasks.py backend/app/schemas/task.py backend/app/api/routes/tasks.py backend/tests/test_task_execution.py
git commit -m "feat(execucao): transicao atomica, janela nos dois lados e guardrails de PRN"
```

---

### Task 15: Board e trilha de auditoria paginada

**Files:**
- Create: `backend/app/schemas/board.py`
- Create: `backend/app/api/routes/board.py`
- Create: `backend/app/api/routes/audit.py`
- Modify: `backend/app/main.py` (incluir os routers)
- Test: `backend/tests/test_board_audit.py`

**Interfaces:**
- Consumes: `TaskService.display_state` (Task 13), `Hospitalization`/`Patient`/`Kennel` (Tasks 7–8), `AuditEntry` (Task 4), `Page`/`paginate` (Task 7).
- Produces:
  - `GET /api/v1/board` → `{"totals": {...}, "rows": [...]}`.
  - `GET /api/v1/audit?entity_type=&entity_id=&limit=&cursor=` → `Page[AuditEntryOut]`, `id DESC`; `tech` → 403.

> O board **não** tem consulta própria de estado: ele chama o mesmo `display_state` da fila. É essa decisão que evita o bug fatal do concorrente — paciente sumindo do painel enquanto a ficha o mostra.

- [ ] **Step 1: Escrever os testes que falham**

Crie `backend/tests/test_board_audit.py`:

```python
from datetime import UTC, datetime, timedelta

from app.models import Task
from tests.factories import (
    make_clinic, make_hospitalization, make_kennel, make_membership, make_patient, make_user,
)
from tests.helpers import bearer, personal_token


async def _clinica_com_tres_estados(session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    agora = datetime.now(UTC)

    cenarios = [
        ("Thor", agora + timedelta(hours=2), 60, "normal"),      # on_time
        ("Nina", agora - timedelta(minutes=10), 60, "normal"),   # due
        ("Mel", agora - timedelta(hours=3), 30, "critical"),     # overdue + crítica
    ]
    for nome, quando, tolerancia, criticidade in cenarios:
        patient = await make_patient(session, clinic=clinic, name=nome)
        hosp = await make_hospitalization(
            session, clinic=clinic, patient=patient, membership=membership
        )
        hosp.kennel_id = kennel.id
        session.add(Task(
            clinic_id=clinic.id, hospitalization_id=hosp.id, title=f"Tarefa de {nome}",
            category="medication", scheduled_for=quando, criticality=criticidade,
            tolerance_minutes=tolerancia, status="pending",
        ))
    await session.flush()
    return clinic, membership


async def test_board_agrupa_por_internacao_com_contadores(client, session):
    clinic, membership = await _clinica_com_tres_estados(session)
    resp = await client.get("/api/v1/board", headers=bearer(personal_token(membership)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["patients"] == 3
    assert body["totals"]["due"] == 1
    assert body["totals"]["overdue"] == 1

    por_paciente = {row["patient_name"]: row for row in body["rows"]}
    assert por_paciente["Mel"]["critical_overdue"] is True
    assert por_paciente["Thor"]["critical_overdue"] is False
    assert por_paciente["Nina"]["counters"]["due"] == 1
    assert por_paciente["Thor"]["next_task"]["title"] == "Tarefa de Thor"


async def test_board_e_fila_concordam_sobre_a_mesma_tarefa(client, session):
    """Mesma fonte de verdade: o board não pode dizer 'no prazo' e a ficha 'atrasada'."""
    clinic, membership = await _clinica_com_tres_estados(session)
    headers = bearer(personal_token(membership))
    fila = (await client.get(
        f"/api/v1/tasks?from={(datetime.now(UTC) - timedelta(hours=6)).isoformat()}"
        f"&to={(datetime.now(UTC) + timedelta(hours=6)).isoformat()}",
        headers=headers,
    )).json()["items"]
    board = (await client.get("/api/v1/board", headers=headers)).json()

    estados_fila = {item["title"]: item["display_state"] for item in fila}
    for row in board["rows"]:
        proxima = row["next_task"]
        if proxima is not None:
            assert estados_fila[proxima["title"]] == proxima["display_state"]


async def test_auditoria_paginada_por_cursor(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="admin")
    headers = bearer(personal_token(membership))
    for indice in range(5):
        await client.post(
            "/api/v1/owners",
            json={"name": f"Tutor {indice}", "phone_e164": "+5511999990000"}, headers=headers,
        )

    vistos: list[int] = []
    cursor = None
    for _ in range(5):
        url = "/api/v1/audit?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=headers)).json()
        vistos.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(vistos) == len(set(vistos))
    assert vistos == sorted(vistos, reverse=True)


async def test_auditoria_filtra_por_entidade(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="vet")
    headers = bearer(personal_token(membership))
    criado = (await client.post(
        "/api/v1/owners", json={"name": "Marina", "phone_e164": "+5511999990000"}, headers=headers
    )).json()

    page = (await client.get(
        f"/api/v1/audit?entity_type=owner&entity_id={criado['id']}", headers=headers
    )).json()
    assert len(page["items"]) == 1
    assert page["items"][0]["action"] == "owner_created"


async def test_tecnico_nao_le_auditoria(client, session):
    clinic = await make_clinic(session)
    user = await make_user(session)
    membership = await make_membership(session, clinic=clinic, user=user, role="tech")
    resp = await client.get("/api/v1/audit", headers=bearer(personal_token(membership)))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_isolamento_de_tenant_no_board(client, session):
    clinic_a, membership_a = await _clinica_com_tres_estados(session)
    clinic_b = await make_clinic(session, slug="clinica-b")
    await make_hospitalization(session, clinic=clinic_b)
    await session.flush()

    body = (await client.get("/api/v1/board", headers=bearer(personal_token(membership_a)))).json()
    assert body["totals"]["patients"] == 3
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_board_audit.py -q`
Expected: `404` em `/api/v1/board` e `/api/v1/audit`.

- [ ] **Step 3: Implementar o schema do board**

Crie `backend/app/schemas/board.py`:

```python
import uuid

from pydantic import BaseModel

from app.schemas.task import TaskOut


class BoardCounters(BaseModel):
    on_time: int = 0
    due: int = 0
    overdue: int = 0


class BoardRow(BaseModel):
    hospitalization_id: uuid.UUID
    patient_name: str
    kennel_name: str | None
    next_task: TaskOut | None
    counters: BoardCounters
    critical_overdue: bool


class BoardTotals(BaseModel):
    patients: int
    due: int
    overdue: int
    on_time_rate: float


class BoardOut(BaseModel):
    totals: BoardTotals
    rows: list[BoardRow]
```

- [ ] **Step 4: Implementar o router do board**

Crie `backend/app/api/routes/board.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.models import Hospitalization, Kennel, Patient, Task
from app.schemas.board import BoardCounters, BoardOut, BoardRow, BoardTotals
from app.schemas.task import TaskOut
from app.services.tasks import TaskService

router = APIRouter(prefix="/api/v1/board", tags=["board"])


@router.get("", response_model=BoardOut)
async def board(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BoardOut:
    now = datetime.now(UTC)
    rows_stmt = (
        sa.select(Hospitalization, Patient.name, Kennel.name)
        .join(Patient, Patient.id == Hospitalization.patient_id)
        .outerjoin(Kennel, Kennel.id == Hospitalization.kennel_id)
        .where(
            Hospitalization.clinic_id == auth.clinic_id,
            Hospitalization.status == "active",
        )
        .order_by(Patient.name)
    )
    internacoes = list((await session.execute(rows_stmt)).all())

    tasks_stmt = sa.select(Task).where(
        Task.clinic_id == auth.clinic_id,
        Task.status == "pending",
        Task.scheduled_for <= now + timedelta(hours=12),
    )
    tarefas = list((await session.execute(tasks_stmt)).scalars())
    por_internacao: dict = {}
    for task in tarefas:
        por_internacao.setdefault(task.hospitalization_id, []).append(task)

    linhas: list[BoardRow] = []
    total_due = total_overdue = total_on_time = 0
    for hospitalization, patient_name, kennel_name in internacoes:
        pendentes = sorted(
            por_internacao.get(hospitalization.id, []), key=lambda item: item.scheduled_for
        )
        counters = BoardCounters()
        critica_atrasada = False
        for task in pendentes:
            # MESMA função da fila: nenhuma segunda fonte de verdade.
            estado = TaskService.display_state(task, now)
            setattr(counters, estado, getattr(counters, estado) + 1)
            if estado == "overdue" and task.criticality == "critical":
                critica_atrasada = True
        total_due += counters.due
        total_overdue += counters.overdue
        total_on_time += counters.on_time
        linhas.append(BoardRow(
            hospitalization_id=hospitalization.id,
            patient_name=patient_name,
            kennel_name=kennel_name,
            next_task=TaskOut.from_task(pendentes[0], now) if pendentes else None,
            counters=counters,
            critical_overdue=critica_atrasada,
        ))

    total = total_due + total_overdue + total_on_time
    return BoardOut(
        totals=BoardTotals(
            patients=len(internacoes), due=total_due, overdue=total_overdue,
            on_time_rate=round(total_on_time / total, 4) if total else 1.0,
        ),
        rows=linhas,
    )
```

- [ ] **Step 5: Implementar o router de auditoria**

Crie `backend/app/api/routes/audit.py`:

```python
import uuid
from datetime import datetime
from typing import Annotated, Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_current_auth, get_session
from app.core.errors import AppError
from app.models import AuditEntry
from app.schemas.pagination import DEFAULT_LIMIT, MAX_LIMIT, Page

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_name: str
    actor_license: str | None
    actor_license_authority: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    payload: dict[str, Any]
    entry_hash: str
    created_at: datetime


@router.get("", response_model=Page[AuditEntryOut])
async def list_audit(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    session: Annotated[AsyncSession, Depends(get_session)],
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    cursor: str | None = None,
) -> Page[AuditEntryOut]:
    if auth.membership is None or auth.membership.role == "tech":
        raise AppError("forbidden", 403)

    stmt = sa.select(AuditEntry).where(AuditEntry.clinic_id == auth.clinic_id)
    if entity_type is not None:
        stmt = stmt.where(AuditEntry.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditEntry.entity_id == entity_id)
    if cursor is not None:
        stmt = stmt.where(AuditEntry.id < int(cursor))
    stmt = stmt.order_by(AuditEntry.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = str(rows[-1].id)
    return Page[AuditEntryOut](
        items=[AuditEntryOut.model_validate(row) for row in rows], next_cursor=next_cursor
    )
```

Registre os dois routers em `create_app()`.

- [ ] **Step 6: Rodar, lint e commit**

```bash
uv run pytest tests/test_board_audit.py -q && uv run pytest -q && uv run ruff check .
git add backend/app/schemas/board.py backend/app/api/routes/board.py backend/app/api/routes/audit.py backend/app/main.py backend/tests/test_board_audit.py
git commit -m "feat(board): visao geral da internacao e trilha de auditoria paginada"
```

Expected: `6 passed` no arquivo, suíte verde, lint limpo.

---

### Task 16: Seed de demonstração, README e smoke end-to-end

**Files:**
- Create: `backend/scripts/seed_demo.py`
- Create: `backend/README.md`
- Test: `backend/tests/test_smoke_e2e.py`

**Interfaces:**
- Consumes: tudo das Tasks 1–15.
- Produces:
  - `uv run python -m scripts.seed_demo` — clínica demo pronta para a demonstração de venda.
  - `backend/README.md` com o passo a passo de subir, testar e semear.
  - Um teste E2E que percorre o fluxo inteiro do produto.

> Este seed é o que o vendedor abre na frente da clínica. Ele precisa mostrar, sem nenhum clique de preparo: paciente crítico com tarefa atrasada, fluidoterapia contínua, PRN com guardrail e cerimônias do dia.

- [ ] **Step 1: Escrever o smoke E2E que falha**

Crie `backend/tests/test_smoke_e2e.py`:

```python
import hashlib
import json
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.core.security import hash_password
from app.models import AuditEntry
from tests.factories import make_clinic, make_kennel, make_membership, make_owner, make_patient, make_user
from tests.helpers import bearer


async def test_fluxo_completo_da_internacao(client, session):
    clinic = await make_clinic(session, slug="demo", station_key_hash=hash_password("estacao-123"))
    vet_user = await make_user(session, email="paula@demo.vet", password_hash=hash_password("senha-123"))
    vet = await make_membership(
        session, clinic=clinic, user=vet_user, role="vet",
        license_number="12345", license_authority="CRMV-SP", pin_hash=hash_password("4321"),
    )
    kennel = await make_kennel(session, clinic=clinic, name="UTI 03")
    owner = await make_owner(session, clinic=clinic, name="Marina Campos")
    patient = await make_patient(session, clinic=clinic, owner=owner, name="Thor")
    await session.flush()

    # 1. Login pessoal
    login = await client.post(
        "/api/v1/auth/login", json={"email": "paula@demo.vet", "password": "senha-123"}
    )
    assert login.status_code == 200
    token = bearer(login.json()["access_token"])

    # 2. Admitir — as cerimônias do dia nascem junto
    admissao = await client.post(
        "/api/v1/hospitalizations",
        json={"patient_id": str(patient.id), "kennel_id": str(kennel.id),
              "vet_membership_id": str(vet.id), "consent_status": "consent_recorded"},
        headers=token,
    )
    assert admissao.status_code == 201
    hospitalization_id = admissao.json()["hospitalization"]["id"]

    detalhe = (await client.get(f"/api/v1/hospitalizations/{hospitalization_id}", headers=token)).json()
    assert sorted(p["name"] for p in detalhe["prescriptions"]) == ["Contato com tutor", "Evolução diária"]

    # 3. Prescrever
    prescricao = await client.post(
        f"/api/v1/hospitalizations/{hospitalization_id}/prescriptions",
        json={"kind": "recurring", "category": "medication", "name": "Dipirona 25 mg/kg IV",
              "frequency_minutes": 480, "duration_hours": 72, "criticality": "normal",
              "first_dose_now": True, "price_minor": 1800,
              "details": {"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"}},
        headers=token,
    )
    assert prescricao.status_code == 201

    # 4. A fila da janela já mostra a primeira dose
    fila = (await client.get("/api/v1/tasks", headers=token)).json()["items"]
    primeira = next(item for item in fila if item["title"].startswith("Dipirona"))
    assert primeira["display_state"] in ("on_time", "due")

    # 5. Executar em modo estação, identificando o operador por PIN
    estacao = await client.post(
        "/api/v1/auth/station", json={"clinic_slug": "demo", "station_key": "estacao-123"}
    )
    assert estacao.status_code == 200
    station_headers = bearer(estacao.json()["access_token"])

    pin = await client.post("/api/v1/auth/pin", json={"pin": "4321"}, headers=station_headers)
    assert pin.status_code == 200
    operador = {**station_headers, "X-Operator-Token": pin.json()["operator_token"]}

    execucao = await client.post(
        f"/api/v1/tasks/{primeira['id']}/execute",
        json={"values": {"note": "sem intercorrência"}}, headers=operador,
    )
    assert execucao.status_code == 200
    assert execucao.json()["status"] == "done"

    # 6. O board reflete na hora, com a mesma fonte da fila
    board = (await client.get("/api/v1/board", headers=token)).json()
    linha = next(row for row in board["rows"] if row["patient_name"] == "Thor")
    assert board["totals"]["patients"] == 1
    assert linha["critical_overdue"] is False

    # 7. A trilha tem nome, registro profissional e a cadeia de hash íntegra
    auditoria = (await client.get("/api/v1/audit", headers=token)).json()["items"]
    execucoes = [item for item in auditoria if item["action"] == "task_executed"]
    assert execucoes[0]["actor_license"] == "12345"
    assert execucoes[0]["actor_license_authority"] == "CRMV-SP"

    entradas = list((await session.execute(
        sa.select(AuditEntry).where(AuditEntry.clinic_id == clinic.id).order_by(AuditEntry.id)
    )).scalars())
    anterior = ""
    for entrada in entradas:
        assert entrada.prev_hash == anterior
        esperado = hashlib.sha256(
            f"{anterior}|{entrada.clinic_id}|{entrada.action}|{entrada.entity_type}|"
            f"{entrada.entity_id}|{json.dumps(entrada.payload, sort_keys=True, default=str)}|"
            f"{entrada.created_at.isoformat()}".encode()
        ).hexdigest()
        assert entrada.entry_hash == esperado
        anterior = entrada.entry_hash
```

> Se a fórmula do hash acima divergir da implementada na Task 4, **corrija o teste para espelhar a implementação** — o que importa é a cadeia ser verificável de fora.

- [ ] **Step 2: Rodar e ver falhar**

Run: `uv run pytest tests/test_smoke_e2e.py -q`
Expected: falha na primeira asserção que ainda não estiver completa (normalmente o login, se alguma task anterior não estiver mergeada).

- [ ] **Step 3: Escrever o seed de demonstração**

Crie `backend/scripts/seed_demo.py`:

```python
"""Clínica demo do PlantãoVet — o que o vendedor abre na frente do cliente.

    uv run python -m scripts.seed_demo
"""

import asyncio
from datetime import UTC, datetime, timedelta

from app.core.db import async_session_factory
from app.core.security import hash_password
from app.models import (
    Clinic, Hospitalization, Kennel, Membership, Owner, Patient, Prescription, Task, User,
)
from app.schemas.prescription import default_tolerance
from app.services.audit import ActorInfo
from app.services.hospitalization import HospitalizationService
from app.services.tasks import TaskService

STATION_KEY = "estacao-123"
SENHA = "senha-123"

EQUIPE = [
    ("Dra. Paula Martins", "paula@demo.vet", "vet", "12345", "CRMV-SP", "1234"),
    ("Marina Coelho", "marina@demo.vet", "tech", None, None, "2345"),
    ("Rafael Souza", "rafael@demo.vet", "admin", None, None, "3456"),
]

PACIENTES = [
    ("Thor", "dog", "UTI 03", 24.3),
    ("Nina", "cat", "Box 07", 4.1),
    ("Mel", "cat", "UTI 01", 3.6),
    ("Bob", "dog", "Box 02", 12.0),
    ("Luna", "cat", "Box 04", 3.9),
]


async def main() -> None:
    async with async_session_factory() as session:
        clinic = Clinic(
            name="Clínica Vida Animal", slug="demo", bed_limit=25, plan_tier="hospital",
            station_key_hash=hash_password(STATION_KEY),
        )
        session.add(clinic)
        await session.flush()

        memberships = {}
        for nome, email, papel, registro, orgao, pin in EQUIPE:
            user = User(name=nome, email=email, password_hash=hash_password(SENHA))
            session.add(user)
            await session.flush()
            membership = Membership(
                clinic_id=clinic.id, user_id=user.id, role=papel,
                license_number=registro, license_authority=orgao, pin_hash=hash_password(pin),
            )
            session.add(membership)
            memberships[papel] = membership
        await session.flush()

        vet = memberships["vet"]
        actor = ActorInfo(
            membership_id=vet.id, name="Dra. Paula Martins",
            license_number="12345", license_authority="CRMV-SP",
        )

        kennels = {}
        for nome in ("UTI 01", "UTI 03", "Box 02", "Box 04", "Box 07"):
            kennel = Kennel(clinic_id=clinic.id, name=nome, area="UTI" if "UTI" in nome else "Geral")
            session.add(kennel)
            kennels[nome] = kennel
        await session.flush()

        agora = datetime.now(UTC)
        for nome, especie, box, peso in PACIENTES:
            owner = Owner(
                clinic_id=clinic.id, name=f"Tutor de {nome}", phone_e164="+5511999990000"
            )
            session.add(owner)
            await session.flush()
            patient = Patient(
                clinic_id=clinic.id, owner_id=owner.id, name=nome, species=especie, weight_kg=peso
            )
            session.add(patient)
            await session.flush()
            hospitalization = Hospitalization(
                clinic_id=clinic.id, patient_id=patient.id, kennel_id=kennels[box].id,
                vet_membership_id=vet.id, consent_status="consent_recorded",
                admitted_at=agora - timedelta(days=2),
            )
            session.add(hospitalization)
            await session.flush()
            await HospitalizationService.create_default_prescriptions(
                session, hospitalization=hospitalization, clinic=clinic, actor=actor
            )

            receitas = [
                dict(kind="recurring", category="medication", name="Dipirona 25 mg/kg IV",
                     frequency_minutes=480, criticality="normal", price_minor=1800,
                     details={"drug": "dipirona", "dose": "25 mg/kg", "route": "IV"}),
                dict(kind="continuous", category="fluids", name="Ringer Lactato",
                     frequency_minutes=120, criticality="normal", price_minor=1200,
                     details={"rate_ml_h": 60}),
                dict(kind="recurring", category="monitoring", name="Pressão arterial",
                     frequency_minutes=720, criticality="critical", price_minor=4500,
                     details={}),
                dict(kind="recurring", category="nutrition", name="Alimentação úmida",
                     frequency_minutes=480, criticality="normal", price_minor=0, details={}),
                dict(kind="prn", category="medication", name="Metadona 0,2 mg/kg IM",
                     frequency_minutes=None, criticality="critical", price_minor=3800,
                     max_doses_24h=4, min_interval_minutes=240,
                     details={"drug": "metadona", "route": "IM"}),
            ]
            for receita in receitas:
                prescription = Prescription(
                    clinic_id=clinic.id, hospitalization_id=hospitalization.id,
                    tolerance_minutes=default_tolerance(
                        receita["criticality"], receita["frequency_minutes"]
                    ),
                    starts_at=agora - timedelta(hours=8), created_by=vet.id, **receita,
                )
                session.add(prescription)
                await session.flush()
                await TaskService.materialize(
                    session, prescription=prescription, clinic=clinic,
                    until=agora + timedelta(hours=48),
                )

        # Uma tarefa crítica deliberadamente atrasada: é o que a demo precisa mostrar.
        atrasada = await session.scalar(
            Task.__table__.select().where(Task.criticality == "critical").limit(1)
        )
        if atrasada is not None:
            await session.execute(
                Task.__table__.update()
                .where(Task.id == atrasada.id)
                .values(scheduled_for=agora - timedelta(hours=3))
            )

        await session.commit()
        print(f"Clínica demo criada · slug=demo · senha={SENHA} · station_key={STATION_KEY}")
        print("PINs: vet 1234 · técnico 2345 · admin 3456")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Escrever o README**

Crie `backend/README.md`:

````markdown
# PlantãoVet — backend

API da internação: prescrição → aprazamento → tarefas, com board, trilha de auditoria
encadeada e dois modos de identidade (pessoal e estação).

## Subir o ambiente

```bash
docker compose up -d postgres     # na raiz do repositório
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

## Testes

```bash
uv run pytest -q          # suíte inteira
uv run ruff check .       # lint
```

## Dados de demonstração

```bash
uv run python -m scripts.seed_demo
```

Cria a clínica **demo** com 5 pacientes internados, prescrições dos três tipos
(recorrente, contínua e PRN), as cerimônias do dia e uma tarefa crítica atrasada.

## Como entrar

**Modo pessoal** (o profissional na própria conta):

```bash
curl -X POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "paula@demo.vet", "password": "senha-123"}'
```

**Modo estação** (computador ou celular compartilhado pela equipe): o dispositivo
entra com a chave da clínica e cada ação clínica exige o PIN do operador.

```bash
curl -X POST localhost:8000/api/v1/auth/station \
  -H 'content-type: application/json' \
  -d '{"clinic_slug": "demo", "station_key": "estacao-123"}'

curl -X POST localhost:8000/api/v1/auth/pin \
  -H "authorization: Bearer $STATION_TOKEN" \
  -H 'content-type: application/json' -d '{"pin": "1234"}'
```

O `operator_token` que volta vale 5 minutos e vai no header `X-Operator-Token` das
mutações clínicas.

## Idioma, moeda e unidades

São da clínica, não do sistema (ADR-0004): `clinics.locale`, `clinics.currency`,
`clinics.unit_system` e `clinics.timezone`. A API nunca devolve texto para exibir —
só códigos de erro estáveis, que o cliente traduz. Para ver uma clínica em inglês,
mude `locale` para `en` e reinternem um paciente: as cerimônias do dia nascem
traduzidas.

## Horários das doses

Cada clínica tem seus horários-âncora em `clinics.anchors`, chaveados por minutos
(`480` = 8/8h → 10:00, 18:00, 02:00). O aprazamento deriva os horários dali; quando
não há âncora para a frequência (ex.: 30 min de UTI), usa offset a partir do início.
````

- [ ] **Step 5: Rodar o seed de verdade**

Run (em `backend/`, com o Postgres de pé):

```bash
uv run alembic upgrade head && uv run python -m scripts.seed_demo
```

Expected: `Clínica demo criada · slug=demo · senha=senha-123 · station_key=estacao-123`

- [ ] **Step 6: Suíte inteira verde**

Run: `uv run pytest -q && uv run ruff check .`
Expected: toda a suíte das 16 tasks passando, lint limpo.

- [ ] **Step 7: Commit**

```bash
git add backend/scripts/seed_demo.py backend/README.md backend/tests/test_smoke_e2e.py
git commit -m "feat(demo): seed da clinica de demonstracao, README e smoke end-to-end"
```
