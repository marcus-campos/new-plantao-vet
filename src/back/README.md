# PlantãoVet — backend

API da internação: prescrição → aprazamento → tarefas, com board, trilha de auditoria
encadeada e dois modos de identidade (pessoal e estação).

## Subir o ambiente

```bash
docker compose up -d postgres     # na raiz de plantaovet/
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

O harness recria o banco `plantaovet_test` no início da sessão e desfaz cada teste
por rollback — nenhum teste vê o dado do outro.

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
mude `locale` para `en` e interne um paciente: as cerimônias do dia nascem
traduzidas.

## Horários das doses

Cada clínica tem seus horários-âncora em `clinics.anchors`, chaveados por minutos
(`480` = 8/8h → 10:00, 18:00, 02:00). O aprazamento deriva os horários dali; quando
não há âncora para a frequência (ex.: 30 min de UTI), usa offset a partir do início.

## Worker

`app/workers/scheduler.py` tem UM job: estender a janela de 48h de tarefas. Não existe
verificador de atraso — "atrasada" é derivada na leitura, então board e ficha nunca
divergem. **No deploy (semana 4)**: o serviço que hospeda o scheduler precisa rodar
com `max-instances=1` e CPU always-allocated, ou o job deve tomar `pg_advisory_lock` —
o Cloud Run escala e rodaria um scheduler por instância.
