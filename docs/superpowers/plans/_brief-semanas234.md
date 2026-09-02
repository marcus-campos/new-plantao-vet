# Brief — Semanas 2 a 4 (o resto do produto)

Contratos compartilhados. Leia junto com `docs/2026-08-31-spec-plantaovet-v1.md`,
`CONTEXT.md` e o código existente em `src/back`.

## Regras que já valem (não reinvente)

- **Identificadores, tabelas, rotas e enums em INGLÊS** (ADR-0004). Testes podem ter
  nome em português.
- **Erros são códigos, nunca prosa**: `raise AppError("codigo", status, **params)`.
  Todo código novo entra em `ERROR_CODES` (`app/core/errors.py`).
- **Tenancy**: `clinic_id` em toda tabela; FK vinda de body validada com
  `get_tenant_obj(session, Model, id, clinic_id)` → 404 `not_found`.
- **Sem DELETE**: desativação por `is_active`/`status`.
- **Auditoria**: toda mutação clínica chama
  `AuditService.record(session, clinic_id=..., actor=..., action=..., entity_type=...,
  entity_id=..., before=..., after=..., extra=...)`, com `AuditService.snapshot(obj)`.
  Colunas sensíveis já são redigidas (`REDACTED`).
- **Operador**: rotas de mutação clínica dependem de `get_operator` (no modo estação
  exige `X-Operator-Token`). Rotas de leitura usam só `get_current_auth`.
- **Paginação**: `Page[T]` + `paginate(...)` de `app/schemas/pagination.py`.
- **Dinheiro**: inteiro em unidade menor (`*_minor`), moeda em `clinics.currency`.
- **Tempo**: `timestamptz` UTC no banco; cálculo local via `zoneinfo` e
  `clinics.timezone`.
- Lint: `ruff` line-length 100. Rode `uv run ruff check .` e `uv run pytest -q`.

## Padrão de arquivo (siga o que já existe)

- Model: `app/models/<nome>.py`, herda `Base`, enums com
  `sa.Enum(..., native_enum=False, values_callable=...)`.
- Schema: `app/schemas/<nome>.py` (pydantic v2, `ConfigDict(from_attributes=True)`).
- Serviço (regra de negócio): `app/services/<nome>.py`, **classe** `XService` com
  `@staticmethod`.
- Rota: `app/api/routes/<nome>.py`, `APIRouter(prefix="/api/v1/...")`.
- Migração: `alembic/versions/<rev>_<slug>.py` com `revision`/`down_revision`
  explícitos. **Escreva à mão** (o autogenerate produz diffs espúrios com os enums).
- Teste: `tests/test_<nome>.py`.

## O que você NÃO deve tocar (o integrador faz)

- `app/models/__init__.py`
- `app/main.py`
- `tests/factories.py`

Se precisar de uma factory nova, defina um helper local no seu arquivo de teste.
As factories existentes: `make_clinic`, `make_user`, `make_membership`, `make_kennel`,
`make_owner`, `make_patient`, `make_hospitalization`, `make_prescription`.
Helpers de auth: `tests/helpers.py` (`bearer`, `personal_token`, `station_token`).
