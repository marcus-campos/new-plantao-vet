# Stack: FastAPI + Postgres + React/Vite SPA

O fundador vem de um ecossistema Django (projetos YziLab e o antigo marketplace), então a escolha de FastAPI é deliberada, não default: prioridade a baixo consumo de recurso por instância e async nativo para escalar a milhares de leitos com infraestrutura enxuta, aceitando perder o admin e as baterias do Django. Frontend é SPA React + Vite + TypeScript consumindo a API: a ficha de tratamento (grade hora × tarefa) é uma UI densa e interativa que justifica SPA, o painel é a mesma app em modo leitura, e o futuro modo offline degradado (cache da agenda do turno via service worker) exige estado no cliente — Jinja2/HTMX foi rejeitado por essas duas razões, e Next.js por não precisarmos de SSR.

## Consequences

- Multi-tenant desde o dia 1: Postgres único com `tenant_id` por linha (clínica), não schema-per-tenant.
- Painel e ficha leem da mesma fila de tarefas na mesma API — nunca caches/fontes independentes (bug fatal documentado no Vet Radar; ver docs/2026-08-31-pesquisa-internacao-veterinaria.md §6).
- Sem admin do Django: telas de gestão precisam ser construídas na própria SPA.
