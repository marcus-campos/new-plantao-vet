# Brief de rascunho — Plano Semana 1 (Fundação) · v3

> Contratos compartilhados entre os rascunhistas das seções do plano. Leia junto com `docs/2026-08-31-spec-plantaovet-v1.md` e `CONTEXT.md`.
> **v3**: identificadores em inglês + i18n nativo (ADR-0004), sobre a v2 (27 achados da revisão adversarial). Onde este brief e qualquer outra fonte divergirem, **este brief manda**.

## Global Constraints

- Python 3.13 · FastAPI (última) · SQLAlchemy 2.0 async + asyncpg · Alembic · pydantic v2 + pydantic-settings · gerenciador `uv` · lint `ruff` · testes `pytest` + `pytest-asyncio` (mode=auto) + `httpx.AsyncClient` (ASGITransport).
- **Todo identificador de código, nome de tabela, rota e valor de enum em INGLÊS** (ADR-0004). O português vive nos catálogos de tradução e nos mockups. Nada de `Internacao`, `/internacoes`, `crmv`.
- Lógica de negócio em **classes de serviço** (`SchedulingService`, `AuditService`, `TaskService`, `HospitalizationService`) — nunca funções soltas de negócio.
- **Frequência em MINUTOS** (`frequency_minutes`) — nunca horas.
- Timestamps: `timestamptz` em UTC no banco; aprazamento, "dia" e "janela" no timezone da clínica (`clinics.timezone`, default `America/Sao_Paulo`) com `zoneinfo`.
- Multi-tenant: `clinic_id` em toda tabela de domínio; TODO endpoint filtra pelo tenant do token.
- Commits: conventional commits, **NUNCA incluir linha Co-Authored-By** (preferência do fundador).
- Nenhum placeholder: todo passo tem código real completo.

### Quatro regras transversais que valem em TODA task

1. **Erros são códigos, nunca prosa** (i18n). Toda resposta de erro é `{"error": {"code": "<snake_case>", "params": {...}}}`, produzida por `AppError(code, status, **params)` e um exception handler registrado em `create_app()`. Nunca `detail="Tarefa já processada"`. Códigos da v1: `invalid_credentials`, `token_expired`, `operator_required`, `pin_locked_out`, `pin_duplicate`, `station_key_rotated`, `task_already_processed`, `early_confirmation_required`, `prn_guardrail`, `consent_reason_required`, `outcome_note_required`, `pending_tasks_confirmation_required`, `not_found`, `forbidden`, `validation_error`.
2. **Tenancy em FK de body**: toda FK recebida no body é carregada com filtro `clinic_id` e responde **404 `not_found`** se for de outro tenant (nunca 403 — não vazar existência). Reforço no banco: `UNIQUE (id, clinic_id)` nos pais e FK composta nos filhos.
3. **Sem DELETE**: nenhuma tabela de domínio tem endpoint de exclusão; desativação é `is_active` (kennels, patients, owners) ou `status` (hospitalizations, tasks).
4. **Teste de isolamento obrigatório por router**: token da clínica A não lê o recurso da B (404) **e** não consegue injetar FK da B no body (404).

## Layout do repositório

```
backend/
  pyproject.toml
  alembic.ini · alembic/
  app/
    main.py                 # create_app(): routers + handler de AppError
    core/config.py          # Settings: DATABASE_URL, JWT_SECRET, ENV
    core/db.py              # engine async, async_session_factory, Base
    core/security.py        # bcrypt hash/verify · JWT HS256 create/decode
    core/errors.py          # AppError + app_error_handler
    i18n/catalog.py         # translate(key, locale, **params) + paridade de chaves
    i18n/pt-BR.json · i18n/en.json
    compliance/__init__.py  # get_profile(name) -> ComplianceProfile
    compliance/br.py        # perfil brasileiro (CFMV)
    models/                 # clinic, user, membership, kennel, owner, patient,
                            #   hospitalization, prescription, task, audit
    schemas/                # auth, kennel, owner, patient, hospitalization,
                            #   prescription, task, board, pagination
    services/               # audit, scheduling, tasks, hospitalization
    api/deps.py             # get_session, get_current_auth, get_operator, get_tenant_obj
    api/routes/             # auth, kennels, owners, patients, hospitalizations,
                            #   prescriptions, tasks, board, audit
    workers/scheduler.py    # AsyncIOScheduler + job único de aprazamento (48h)
  scripts/seed_demo.py
  tests/                    # conftest.py, factories.py, test_*.py
docker-compose.yml          # postgres:16
```

## Modelos (campos EXATOS — spec §4)

Enums (str, valores exatos, em inglês):
- `role: vet|tech|admin`
- `unit_system: metric|imperial`
- `hospitalization.status: active|discharged|died|left_ama`
- `consent_status: consent_recorded|emergency_no_consent`
- `prescription.kind: recurring|continuous|prn`
- `prescription.category: medication|fluids|monitoring|nutrition|care|procedure`
- `criticality: normal|critical`
- `task.status: pending|done|partial|not_done|cancelled`
- `OutcomeReason: refused|fasting|unavailable|vet_order|other`

`clinics.anchors` (jsonb) default — chaveado por **minutos**:
```json
{"1440": ["10:00"], "720": ["10:00", "22:00"], "480": ["10:00", "18:00", "02:00"], "360": ["10:00", "16:00", "22:00", "04:00"]}
```

`clinics.default_prescriptions` (jsonb) default — cerimônias criadas automaticamente na admissão:
```json
[
  {"name_key": "ceremony.owner_contact", "category": "care", "kind": "recurring", "frequency_minutes": 1440, "criticality": "normal", "anchor": "16:00"},
  {"name_key": "ceremony.daily_progress_note", "category": "care", "kind": "recurring", "frequency_minutes": 1440, "criticality": "normal", "anchor": "08:00"}
]
```
`name_key` é chave de catálogo: o `name` gravado na prescrição é `translate(name_key, clinic.locale)` — as cerimônias são conteúdo NOSSO, então são traduzidas na criação; nomes digitados pela clínica nunca são.

Tolerância default por criticidade (ISMP), aplicada na criação se o cliente não enviar `tolerance_minutes`: `critical` → 30 · `normal` → 60 · `normal` com `frequency_minutes >= 1440` → 120.

**Índices na migração** (spec §4): `tasks(clinic_id, status, scheduled_for)`; UNIQUE parcial `tasks(prescription_id, scheduled_for) WHERE prescription_id IS NOT NULL`; `audit_entries(clinic_id, id DESC)`; `audit_entries(clinic_id, entity_type, entity_id)`; `hospitalizations(clinic_id, status)`; UNIQUE `memberships(clinic_id, user_id)`; UNIQUE `(id, clinic_id)` em `hospitalizations` e `prescriptions`.

## Interfaces compartilhadas (assinaturas EXATAS — usar literalmente)

```python
# app/core/errors.py
class AppError(Exception):
    def __init__(self, code: str, status_code: int = 400, **params: Any) -> None: ...
# handler devolve JSONResponse(status_code, {"error": {"code": code, "params": params}})

# app/i18n/catalog.py
def translate(key: str, locale: str, **params: Any) -> str: ...
# Carrega app/i18n/<locale>.json com fallback para 'pt-BR' (idioma-fonte);
# chave ausente levanta KeyError (falha no teste, não em produção silenciosa).
def catalog_keys(locale: str) -> set[str]: ...

# app/compliance/__init__.py
@dataclass(frozen=True)
class ComplianceProfile:
    name: str
    license_authority_label_key: str
    requires_daily_progress_note: bool
    retention_years: int
def get_profile(name: str) -> ComplianceProfile: ...

# app/services/audit.py
@dataclass
class ActorInfo:
    membership_id: uuid.UUID | None
    name: str
    license_number: str | None
    license_authority: str | None

class AuditService:
    REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}

    @staticmethod
    def snapshot(entity: Any) -> dict: ...
    # dict das colunas do model, excluindo REDACTED; uuid/datetime/Decimal como str.

    @staticmethod
    async def record(session: AsyncSession, *, clinic_id: uuid.UUID, actor: ActorInfo | None,
                     action: str, entity_type: str, entity_id: uuid.UUID | None,
                     before: dict | None = None, after: dict | None = None,
                     extra: dict | None = None) -> None: ...
    # payload = {"before": before, "after": after, "extra": extra}
    # HASH: entry_hash = sha256(f"{prev_hash}|{clinic_id}|{action}|{entity_type}|{entity_id}|{json canônico do payload}|{created_at.isoformat()}").hexdigest()
    #   prev_hash = entry_hash da última entrada da MESMA clínica (ou "" na primeira).
    # audit_entries é append-only: a migração cria TRIGGER no Postgres que levanta
    #   exceção em UPDATE e DELETE na tabela.

# app/api/deps.py
@dataclass
class AuthContext:
    kind: Literal["personal", "station"]
    clinic_id: uuid.UUID
    membership: Membership | None   # None quando kind == "station"

async def get_session() -> AsyncIterator[AsyncSession]: ...
async def get_current_auth(...) -> AuthContext: ...
# Bearer JWT, exp 12h. kind=station: valida claim station_key_version contra
#   clinics.station_key_version → AppError("station_key_rotated", 401) se divergir.
async def get_operator(...) -> ActorInfo: ...
# pessoal → ActorInfo do próprio membership.
# estação → exige header X-Operator-Token (JWT de 5 min emitido por POST /auth/pin);
#   ausente/expirado → AppError("operator_required", 403).
async def get_tenant_obj(session: AsyncSession, model: type, obj_id: uuid.UUID,
                         clinic_id: uuid.UUID) -> Any: ...
# 404 AppError("not_found") quando o objeto não existe NAQUELE tenant.

# app/services/scheduling.py
class SchedulingService:
    @staticmethod
    def generate(prescription: Prescription, clinic: Clinic, until: datetime) -> list[Task]: ...
```

### Contrato de `SchedulingService.generate` (puro, sem I/O) — regras EXATAS

1. `kind == "prn"` → retorna `[]`.
2. Horizonte: de `prescription.starts_at` até `min(until, prescription.ends_at or until)`; `ends_at` deriva de `duration_hours` na criação.
3. **Com âncora**: se `str(frequency_minutes)` existe em `clinic.anchors`, itera **dia a dia no calendário local da clínica**: para cada dia, os horários-âncora ordenados como hora-do-dia; quando as âncoras do dia se esgotam, avança para o dia seguinte (é isso que resolve admissão às 23h com âncoras 10/18/02 — a próxima é 02:00 do dia seguinte). Só emite horários `>= starts_at`.
4. **Sem âncora**: `starts_at + N * frequency_minutes`, N = 0, 1, 2…
5. **`first_dose_now`**: emite uma tarefa em `starts_at` e **descarta a primeira âncora seguinte** se `(âncora − starts_at) < frequency_minutes − tolerance_minutes`.
6. `kind == "continuous"` → tarefas de checagem: `title = translate("task.check", clinic.locale, name=prescription.name)` (catálogo pt-BR: `"Checagem: {name}"`).
7. **DST / hora local inválida**: ao converter hora local → UTC, se a hora não existe (adiantamento), empurra para a próxima hora válida; se é ambígua (atraso), usa `fold=0`.
8. Idempotência: quem persiste usa `INSERT ... ON CONFLICT (prescription_id, scheduled_for) DO NOTHING`. `scheduled_for` de tarefas já criadas é **congelado**: mudar âncoras/timezone só afeta horários além do horizonte já gerado.

```python
# app/services/tasks.py
class TaskService:
    @staticmethod
    def display_state(task: Task, now: datetime) -> str: ...
    # pending e now < scheduled_for                                → "on_time"
    # pending e scheduled_for <= now <= scheduled_for + tolerância → "due"
    # pending e now > scheduled_for + tolerância                   → "overdue"
    # demais status → o próprio status ("done", "partial", "not_done", "cancelled")
```

### Transição de estado de tarefa — ATÔMICA (obrigatório)

Executar / marcar não-feita NUNCA é "carrega, checa, salva". É um único UPDATE condicional:

```python
stmt = (
    update(Task)
    .where(Task.id == task_id, Task.clinic_id == clinic_id, Task.status == "pending")
    .values(status="done", executed_at=..., executed_by=..., ...)
    .returning(Task)
)
row = (await session.execute(stmt)).scalar_one_or_none()
if row is None:
    raise AppError("task_already_processed", 409)
```

Cada task que muda estado de tarefa **precisa de um teste de corrida**: duas execuções concorrentes na mesma tarefa → uma 200, uma 409.

### Regras de execução

- `retroactive=true` exige **`performed_at`** no body (hora real do procedimento) → grava em `executed_at`; a auditoria registra os dois instantes (`extra={"performed_at":…, "recorded_at":…}`).
- `now < scheduled_for − tolerance_minutes` → exige `confirm_early=true`, senão **409 `early_confirmation_required`**; com a confirmação, grava `early=true`.
- `partial=true` exige `values.dose_given`.
- `not-done` exige `reason` do enum; `other` exige `values.outcome_detail`.
- `POST /tasks/ad-hoc` com `prescription_id` de `kind=prn`: valida `min_interval_minutes` contra a última execução e `max_doses_24h` nas últimas 24h → se violar, **409 `prn_guardrail`** com os dados em `params`; o cliente reenvia com `override=true`, que executa e audita (`extra={"override": true, "guardrail": …}`). Nunca bloqueio duro.

### Envelope de paginação

```python
# app/schemas/pagination.py
class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None
# ?limit= (default 50, máx 200) e ?cursor= (opaco: id serializado). /audit ordena id DESC.
```

### Testes de i18n exigidos (Task 1)

```python
def test_catalogos_tem_as_mesmas_chaves():
    assert catalog_keys("pt-BR") == catalog_keys("en")

async def test_nenhum_erro_devolve_prosa(client, ...):
    # dispara os erros conhecidos e valida que todo error.code casa ^[a-z][a-z0-9_]*$
```

## Formato de tarefa do plano (obrigatório)

Cada tarefa segue exatamente o formato do skill writing-plans: cabeçalho `### Task N: Nome`, blocos **Files** (Create/Modify/Test com caminhos exatos), **Interfaces** (Consumes/Produces com assinaturas), e passos checkbox de 2–5 min: escrever teste que falha (código real) → rodar e ver falhar (comando + saída esperada) → implementação mínima (código real completo) → rodar e ver passar → commit (comando git com mensagem). Proibido: TODO/TBD, "adicione validação apropriada", "similar à task N", referência a símbolo não definido em nenhuma task.
