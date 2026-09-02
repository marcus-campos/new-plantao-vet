# Guia de estilo das telas do PlantãoVet

Todas as telas são arquivos `.dc.html` no formato Design Component. Copie a estrutura abaixo **exatamente** — a linha do `support.js` não pode ser removida nem inlined.

```html
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,700;12..96,800&family=Instrument+Sans:wght@400;500;600&display=swap">
  <style>
    body { margin: 0; font-family: "Instrument Sans", "Segoe UI", system-ui, sans-serif; }
    a { color: #0C6B58; } a:hover { color: #0A5847; }
  </style>
</helmet>
<div style="width: 1440px; height: 900px; ...">…</div>
</x-dc>
</body>
</html>
```

Sem `<script data-dc-script>` (as telas são estáticas). Todo estilo é **inline** nos elementos.

## Tamanhos

- Tela web: `width: 1440px; height: 900px` no elemento raiz, com `overflow: hidden`.
- Tela de app: `width: 390px; height: 844px`, com um `<div style="height: 54px"></div>` no topo (espaço da status bar real — **nunca desenhe status bar falsa nem teclado falso**).

## Cores (tema claro; use exatamente estes valores)

| Uso | Hex |
|---|---|
| Fundo da página | `#F3F6F4` |
| Superfície (cards, barras) | `#FFFFFF` |
| Superfície sutil (cabeçalho de tabela, caixas de apoio) | `#F8FAF9` |
| Texto principal | `#17251F` |
| Texto secundário | `#4A5A52` |
| Texto terciário / legendas | `#7C8B83` |
| Texto apagado (desativado) | `#9AA9A1` |
| Verde primário (ações, marca) | `#0C6B58` |
| Verde escuro (texto sobre tinta clara) | `#0A5847` |
| Tinta verde clara (chips, destaques) | `#E4F0EB` |
| Borda | `#DFE7E2` · borda interna de tabela `#EDF2EF` |
| Sucesso / no prazo | texto `#1F7A4D` · fundo `#E3F2E9` · faixa `#9CCBB0` |
| Atenção | texto `#8F5D0B` · fundo `#F7EDD8` · faixa `#D9A84E` |
| Atraso / erro | texto `#A83A31` · fundo `#F9E6E3` · faixa/borda `#E9C4BF` |

Tela de app em tema escuro (só a de gravação de áudio usa): fundo `#0F1714`, superfície `#16211C`, borda `#263630`, texto `#E6EEE9`, secundário `#A5B5AC`, verde `#46B195`.

## Tipografia

- Display (números grandes, nomes de tela, logo): `'Bricolage Grotesque', 'Segoe UI', system-ui, sans-serif`, weight 700/800.
- Texto: `Instrument Sans` (já é a fonte do body), weights 400/500/600.
- Rótulos de seção: `font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 600/700`.
- Qualquer coluna de números, horário ou dinheiro leva `font-variant-numeric: tabular-nums`.

## Padrões de componente (copie das telas existentes)

- **Barra superior web**: `background: #FFFFFF; border-bottom: 1px solid #DFE7E2; padding: 12px 28px`, com o logo `Plantão` + `<span style="color: #0C6B58">Vet</span>` (Bricolage 19px/800), o nome da tela em `#7C8B83` 14px à esquerda, e à direita chips de contexto + relógio `18:10`.
- **Card**: `background: #FFFFFF; border: 1px solid #DFE7E2; border-radius: 10px` (12px em cards de formulário), `padding: 16px–20px`.
- **Chip de status**: `border-radius: 999px; padding: 6px 14px; font-size: 13px; font-weight: 600` com o par de cores da tabela acima.
- **Botão primário**: `background: #0C6B58; color: #FFFFFF; border-radius: 8px; padding: 9px 16px (barra) ou 13px 24px (formulário); font-weight: 600/700`.
- **Botão secundário**: `background: #FFFFFF; border: 1px solid #DFE7E2; color: #4A5A52`.
- **Campo de formulário**: rótulo em uppercase 12px `#4A5A52`, campo `border: 1px solid #DFE7E2; border-radius: 8px; padding: 11px 14px; font-size: 15px; background: #FFFFFF`. O campo em foco leva `border: 2px solid #0C6B58`.
- **Toggle ligado**: pílula `52×30`, `background: #0C6B58`, `justify-content: flex-end`, bolinha branca `24×24`. Desligado: `background: #DFE7E2`, sem `justify-content`.
- **Tabela**: cabeçalho `background: #F8FAF9; border-bottom: 1px solid #DFE7E2`, rótulos uppercase 11px `#7C8B83`; linhas em `display: grid` com as mesmas colunas do cabeçalho, `border-bottom: 1px solid #EDF2EF`, `font-size: 14px`.
- **Rodapé explicativo** (opcional, ajuda o cliente a entender a tela): `border-top: 1px solid #DFE7E2; background: #FFFFFF; padding: 12px 28px 16px; color: #7C8B83; font-size: 12.5px`.
- **Layout**: sempre `display: flex`/`grid` com `gap`. Nunca margens soltas entre irmãos.
- **Ícones**: SVG inline, traço 2px, `stroke-linecap="round"`, 16–22px. **Nunca emoji.**
- Alvos de toque nas telas de app: mínimo 44px de altura.

## Dados (mantenha coerência entre telas)

- Clínica: **Clínica Vida Animal**, plano Hospital (25 leitos), 11 internados, fuso São Paulo, idioma pt-BR, moeda BRL.
- Data e hora simuladas: **30 de agosto, 18:10** (virando o plantão diurno 07–19h para o noturno 19–07h).
- Equipe: **Dra. Paula Martins** (vet, CRMV-SP 12345, PIN 1234), **Marina Coelho** (técnica, PIN 2345, iniciais MC), **João Ribeiro** (técnico, iniciais JR), **Rafael Souza** (admin, PIN 3456).
- Pacientes: **Thor** (canino SRD, 24,3 kg, UTI 03, crítico, jejum a partir de 22h), **Nina** (felino, Box 07, glicemia atrasada), **Mel** (felino, UTI 01), **Bob** (canino, Box 02), **Luna** (felino, Box 04), **Fred** (canino, Box 01).
- Horários-âncora da clínica: 24h→10:00 · 12h→10:00 e 22:00 · 8h→10:00, 18:00 e 02:00 · 6h→10:00, 16:00, 22:00 e 04:00.
- Tolerâncias padrão: crítica 30 min · normal 60 min · diária 120 min.
- Prescrições do Thor: Dipirona 25 mg/kg IV q8h; Fluidoterapia Ringer Lactato 60 ml/h contínua com checagem q2h; Ondansetrona 0,5 mg/kg IV q12h; Alimentação úmida q8h; Pressão arterial q12h (crítica); Metadona 0,2 mg/kg IM PRN (máx 4/24h, intervalo mínimo 4h).
- Preços: dipirona R$ 18,00 · ondansetrona R$ 22,00 · metadona R$ 38,00 · pressão arterial R$ 45,00 · checagem de bomba R$ 12,00 · diária UTI R$ 280,00 · diária geral R$ 165,00.

## Escrita

Português do Brasil, tom direto e sem jargão de software. Fale como a clínica fala: "box", "plantão", "tutor", "prescrição", "evolução". Nada de "usuário", "registro do sistema", "entidade". Frases curtas. O rodapé de cada tela pode explicar em uma linha o que aquilo resolve na rotina.
