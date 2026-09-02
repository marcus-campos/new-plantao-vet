# PlantãoVet

A ficha de internação digital que faz o plantão passar direito.

## Onde está o quê

```
src/back      API FastAPI + Postgres — prescrição → aprazamento → tarefas, board, auditoria
src/front     Web React + Vite (responsiva: monitor, notebook, tablet)
src/mobile    App do plantonista em Expo (celular e tablet)
design/telas  28 mockups das telas, publicados como canvas
docs/         spec, ADRs, pesquisa de mercado e o plano da semana 1
```

## Subir tudo (Docker) — um comando

```bash
docker compose up -d --build
```

- **Web**: http://localhost:8080
- **API**: http://localhost:8000 (docs em `/docs`)
- Migrações e a clínica demo rodam sozinhas no boot (`SEED_DEMO=true`); o seed é
  idempotente, então subir de novo não duplica nada.

Para derrubar tudo e zerar o banco: `docker compose down -v`.

## Subir para desenvolver (sem Docker na app)

```bash
docker compose up -d postgres

cd src/back && uv sync && uv run alembic upgrade head
uv run python -m scripts.seed_demo          # clínica demo
uv run uvicorn app.main:app --reload        # :8000

cd ../front && npm install && npm run dev    # :5173

cd ../mobile && npm install && npx expo start
```

Entrar na demo: `paula@demo.vet` / `senha-123`. Modo estação: clínica `demo`, chave
`estacao-123`, PINs 1234 (vet), 2345 (técnica), 3456 (admin).

## Documentos

- `docs/2026-08-31-spec-plantaovet-v1.md` — o produto inteiro, com as regras de domínio
- `docs/adr/` — stack, app companion, auditoria append-only, i18n nativo
- `docs/2026-08-31-pesquisa-internacao-veterinaria.md` — 61 fontes sobre a rotina real,
  concorrentes, CFMV e as lições da medicina humana
- `CONTEXT.md` — glossário bilíngue: identificador em inglês, termo em português
