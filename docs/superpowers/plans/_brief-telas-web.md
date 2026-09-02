# Brief — telas do web (src/front)

## O que já existe e você DEVE reusar

- `src/api/client.ts` — `api.<metodo>()` com todos os endpoints já prontos, e o helper
  `asList(x)` que normaliza resposta que pode vir como array ou `Page`.
  **Não crie fetch novo.** Se faltar um método, avise no relatório em vez de duplicar.
- `src/api/types.ts` — todos os tipos. Importe de lá.
- `src/components/ui.tsx` — `Card`, `Button`, `Field`, `inputStyle`, `StatePill`,
  `stateColors`, `ErrorBanner`, `useApiErrorMessage`.
- `src/components/PinDialog.tsx` — no modo estação, a mutação devolve
  `ApiError.code === "operator_required"`; abra o `PinDialog` e refaça a chamada.
- `src/hooks/useSession.tsx` — `useSession()` dá `session`, `needsOperator`.
- `src/index.css` — tokens de cor em CSS vars (`--primary`, `--ink`, `--late`, …).
  Use SEMPRE as vars, nunca hex solto.
- `src/layout.css` — classes prontas: `.chip`, `.chip-on`, `.chip-group`,
  `.chip-stacked`, `.chip-hint`, `.modal-backdrop`, `.modal-card`, `.form-grid-2`,
  `.board-stats`, `.tabular` (para números).

## Regras

- **Responsivo de verdade**: ≥1280px duas colunas, 768–1279 tablet (laterais viram
  blocos empilhados), <768 uma coluna. Nada de largura fixa. Tabela larga vai dentro de
  um contêiner com `overflow-x: auto` — o corpo da página nunca rola de lado.
- **Toque**: alvo mínimo 44px (a mesma tela roda em tablet no balcão).
- **i18n obrigatório**: nenhum texto literal em componente. Crie
  `src/i18n/extra/<seu-nome>.pt-BR.json` e `src/i18n/extra/<seu-nome>.en.json` com as
  MESMAS chaves (o loader junta sozinho). Prefixe as chaves com o nome da sua área.
- **Erros**: `useApiErrorMessage()` traduz o código; a API nunca manda prosa.
- **Dinheiro**: valores vêm em unidade menor (centavos). Formate com
  `new Intl.NumberFormat(i18n.language, { style: "currency", currency })`.
- **Datas**: `new Intl.DateTimeFormat(i18n.language, …)`.
- TypeScript estrito: `npx tsc --noEmit` e `npm run build` têm de passar.
  Sem `any`. Sem parameter properties em classe (`erasableSyntaxOnly` está ligado).

## Arquivos que você NÃO pode tocar (o integrador cuida)

`src/App.tsx`, `src/api/client.ts`, `src/api/types.ts`, `src/i18n/index.ts`,
`src/i18n/pt-BR.json`, `src/i18n/en.json`, `src/layout.css`, `src/components/ui.tsx`.

Seus arquivos: `src/pages/<SuaTela>.tsx`, componentes próprios em
`src/components/<seu-prefixo><Nome>.tsx`, CSS próprio em `src/styles/<seu-nome>.css`
(importe no topo do seu .tsx), e os dois JSON de i18n.

Exporte cada tela como named export (`export function MinhaTela()`), sem default.
