# PlantãoVet Semana 1 — Fundação · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar a API núcleo do PlantãoVet — tenancy, i18n nativo, dois modos de identidade, pacientes/internações, prescrição → aprazamento → tarefas com execução atômica, board e trilha de auditoria encadeada — pronta para a UI da semana 2 consumir.

**Architecture:** Monolito async FastAPI multi-tenant (Postgres único, `clinic_id` por linha) com lógica de negócio em classes de serviço (`SchedulingService` puro e testável sem I/O, `TaskService`, `AuditService`). Um único worker APScheduler mantém uma janela rolante de 48h de tarefas aprazadas; "atrasada" nunca é persistida — é computada na leitura, de modo que board e ficha jamais divergem. Toda mutação clínica grava snapshots before/after numa trilha append-only imposta por trigger e encadeada por hash. A API é internacionalizada por construção: identificadores e enums em inglês, erros como códigos estáveis (nunca prosa), armazenamento canônico (UTC, SI, ISO 4217, E.164) e catálogos `pt-BR`/`en` com paridade garantida por teste.

**Tech Stack:** Python 3.13 · FastAPI · SQLAlchemy 2.0 async + asyncpg · Alembic · pydantic v2 · APScheduler · pytest/pytest-asyncio/httpx · uv · ruff · Postgres 16 (docker compose local).

**Spec:** `docs/2026-08-31-spec-plantaovet-v1.md` (produto, domínio, i18n §3, schema §4, API §5) · contratos exatos em `docs/superpowers/plans/_drafting-brief-semana1.md` · glossário bilíngue em `CONTEXT.md` · decisões em `docs/adr/` (0001 stack, 0002 app companion, 0003 auditoria append-only, 0004 i18n nativo) · pesquisa de mercado em `docs/2026-08-31-pesquisa-internacao-veterinaria.md`.

## Global Constraints

- Python **3.13**; dependências via `uv`; lint `ruff` (line-length 100, target py313); testes `pytest` com `asyncio_mode = "auto"`.
- **Todo identificador de código, nome de tabela, rota e valor de enum em inglês** (ADR-0004). O português vive nos catálogos de tradução e na UI.
- Lógica de negócio **em classes de serviço**, nunca funções soltas de negócio.
- Frequência de prescrição em **minutos** (`frequency_minutes`). Âncoras da clínica chaveadas por minutos: `{"1440": ["10:00"], "720": ["10:00","22:00"], "480": ["10:00","18:00","02:00"], "360": ["10:00","16:00","22:00","04:00"]}`.
- Timestamps `timestamptz` em UTC no banco; aprazamento, "dia" e "janela" no timezone da clínica (`clinics.timezone`, default `America/Sao_Paulo`) via `zoneinfo`.
- Janelas de tolerância (ISMP): `critical` → 30 min · `normal` → 60 min · `normal` com `frequency_minutes >= 1440` → 120 min.
- **Erros são códigos, nunca prosa**: `{"error": {"code": "<snake_case>", "params": {...}}}` via `AppError`. Um teste valida que todo código casa `^[a-z][a-z0-9_]*$`.
- **Armazenamento canônico**: UTC, unidades SI (kg, °C), dinheiro em unidade menor (`price_minor`) + `clinics.currency` (ISO 4217), telefone em E.164, enums como códigos neutros. Formatação é do cliente.
- **Tenancy em FK de body**: toda FK recebida no body é carregada com filtro `clinic_id` e responde **404 `not_found`** se for de outro tenant (nunca 403). Reforço no banco: `UNIQUE (id, clinic_id)` nos pais + FK composta nos filhos.
- **Sem DELETE** em nenhuma tabela de domínio: desativação é `is_active` (kennels, patients, owners) ou `status` (hospitalizations, tasks).
- **Transição de estado de tarefa é atômica**: `UPDATE ... WHERE status='pending' ... RETURNING`; zero linhas → `409 task_already_processed`. Toda task que muda estado de tarefa tem teste de corrida.
- **Auditoria**: `AuditService.record` com `before`/`after`; `REDACTED = {"phone_e164", "tax_id", "password_hash", "pin_hash", "station_key_hash"}` nunca entra no payload (LGPD — a trilha é inapagável); `entry_hash` encadeia com `prev_hash` da última entrada **da mesma clínica**.
- **Compliance é perfil por país**: nenhuma regra do CFMV fora de `app/compliance/br.py`.
- **Teste de isolamento de tenant obrigatório em cada router**: leitura cruzada → 404 **e** injeção de FK cruzada no body → 404.
- Commits: conventional commits; **nunca** incluir linha `Co-Authored-By`.

## File Structure

```
backend/
  pyproject.toml              # uv, deps, ruff + pytest config
  alembic.ini · alembic/      # migrações (env.py async lendo settings.database_url)
  app/
    main.py                   # create_app(): routers + handler de AppError
    core/config.py            # Settings (DATABASE_URL, JWT_SECRET, ENV)
    core/db.py                # engine async, async_session_factory, Base
    core/security.py          # bcrypt hash/verify · JWT HS256 create/decode
    core/errors.py            # AppError + app_error_handler
    i18n/catalog.py           # translate(key, locale, **params) · catalog_keys(locale)
    i18n/pt-BR.json · en.json # catálogos com paridade garantida por teste
    compliance/__init__.py    # get_profile(name) -> ComplianceProfile
    compliance/br.py          # perfil brasileiro (CFMV)
    models/                   # clinic, user, membership, kennel, owner, patient,
                              #   hospitalization, prescription, task, audit
    schemas/                  # auth, kennel, owner, patient, hospitalization,
                              #   prescription, task, board, pagination
    services/                 # audit, scheduling, tasks, hospitalization
    api/deps.py               # get_session, get_current_auth, get_operator, get_tenant_obj
    api/routes/               # auth, kennels, owners, patients, hospitalizations,
                              #   prescriptions, tasks, board, audit
    workers/scheduler.py      # AsyncIOScheduler + job único de aprazamento (48h)
  scripts/seed_demo.py        # clínica demo completa para o piloto e a demo de venda
  tests/                      # conftest.py (harness), factories.py, test_*.py
docker-compose.yml            # postgres:16
```

Cada arquivo tem uma responsabilidade: models só mapeiam tabelas; schemas só validam entrada/saída; services carregam a regra clínica (e são testáveis sem HTTP); routes só orquestram (auth → serviço → auditoria → resposta).

## Ordem das tarefas

As 16 tarefas são sequenciais — cada uma termina com testes verdes e um commit. Blocos:

| Tarefas | Bloco |
|---|---|
| 1–4 | Fundação técnica: scaffold + erros como códigos + i18n, banco/migrações/harness, identidade e tenant + compliance profile, auditoria encadeada |
| 5–8 | Acesso e cadastro: auth pessoal, modo estação endurecido, owners/patients/kennels, hospitalizations |
| 9–12 | Coração clínico: prescriptions, aprazamento puro, persistência idempotente + worker, suspensão e titulação |
| 13–16 | Operação: fila por janela, execução de tarefas, board e auditoria, seeds + smoke E2E |

**Checkpoint externo (durante a semana, sem bloquear):** fotos das fichas de papel reais das clínicas parceiras + 30 min com um veterinário para confirmar quatro decisões clínicas — `category` da prescrição, `first_dose_now`, titulação de fluidoterapia por versionamento e as cerimônias default do dia (spec §8, risco 1). Migrações seguem normalmente; o que a conversa mudar entra como migração adicional.

---
