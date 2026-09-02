# PlantãoVet — web

SPA React + Vite + TypeScript. Desktop-first, mas **responsiva de verdade**: a mesma
clínica opera num monitor de 27", num notebook e num tablet no balcão.

## Rodar

```bash
npm install
cp .env.example .env      # aponte VITE_API_URL para a API
npm run dev               # http://localhost:5173
```

A API precisa estar de pé (`src/back`) e liberar a origem do front em `cors_origins`.

## Build e checagens

```bash
npm run build     # tsc -b + vite build
npx tsc --noEmit  # só typecheck
```

## Como está organizado

- `src/api/` — tipos do contrato e cliente HTTP. **Todo erro da API é um código**
  (`ApiError.code`), nunca prosa: quem traduz é o front (ADR-0004).
- `src/i18n/` — catálogos `pt-BR` (idioma-fonte) e `en`, com as mesmas chaves.
- `src/hooks/useSession.tsx` — os dois modos de identidade: pessoal (conta própria) e
  estação (dispositivo da clínica, com PIN a cada ato clínico).
- `src/pages/` — Login, Board (painel), TreatmentSheet (ficha) e NewPrescription.
- `src/layout.css` — os pontos de quebra: ≥1280 duas colunas, 768–1279 tablet,
  <768 uma coluna. A grade de horários rola dentro do próprio contêiner; o corpo da
  página nunca rola de lado.

## Modo estação

Quando a sessão é de estação, a primeira mutação clínica devolve `operator_required`
e o front abre o teclado de PIN. O `operator_token` vale 5 minutos e vai no header
`X-Operator-Token`.
