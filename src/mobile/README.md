# PlantãoVet — app do plantonista

App companion em Expo/React Native (ADR-0002). Roda em **celular e tablet**: quem está
entre os boxes vê o turno, dá baixa nas tarefas e se identifica por PIN.

Não é a ficha inteira — grade completa, prescrição e gestão vivem no navegador.

## Rodar

```bash
npm install
cp .env.example .env    # aponte para o IP da máquina, não localhost
npx expo start
```

## Checagens

```bash
npx tsc --noEmit    # typecheck
npx expo-doctor     # sanidade do projeto Expo
```

## Telas

- **Entrar** — conta própria, ou "este celular é da clínica" (modo estação).
- **Meu turno** — a fila da janela, com atraso em destaque. No tablet vira duas colunas
  em vez de esticar a linha.
- **Tarefa** — confirmar, ou marcar não realizada com motivo padronizado.
- **PIN** — no modo estação, identifica quem executou antes de gravar o ato clínico.
  Teclas de 72px: a mão está de luva.

## Identidade

Modo pessoal usa a conta do profissional. Modo estação é o caso da clínica pequena com
um único celular: o aparelho fica logado na clínica e cada baixa pede o PIN. Os dois
gravam nome e registro profissional na trilha de auditoria.
