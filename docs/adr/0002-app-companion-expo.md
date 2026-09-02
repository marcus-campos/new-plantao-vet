# App móvel na v1: companion em Expo/React Native, com dois modos de identidade

O app Android/iOS faz parte da v1 — notas de áudio do plantonista e alertas de tarefa crítica não funcionam num desktop fixo. Decidimos que ele é um **companion de bolso** (tarefas do turno, push de crítica atrasada, baixa de tarefa, nota de áudio, ficha resumida), nunca paridade com a web: a grade completa, prescrição e gestão vivem no navegador. Tecnologia: **Expo/React Native** — mesma mentalidade TS+React do front web, um código para as duas lojas, push/áudio maduros no ecossistema e atualização OTA (EAS Update) sem esperar revisão de loja. Rejeitados: Capacitor (push/áudio inferiores), PWA (push iOS frágil e o requisito era loja), ficha completa mobile (dobra o esforço e nenhum concorrente resolveu essa UX nem com iPad).

Identidade em dois modos, nos dois formatos de dispositivo: **modo pessoal** (conta própria + biometria) e **modo estação** (dispositivo logado na clínica, PIN de operador a cada ação) — porque clínica pequena usa um único celular compartilhado pelo plantão. Ambos gravam nome + CRMV na trilha de auditoria.

## Consequences

- Conta Apple Developer deve ser aberta imediatamente (verificação leva dias); o faturamento em 30 dias não pode depender da revisão da App Store — piloto roda com TestFlight (iOS) e APK/faixa interna (Android).
- Em modo estação, o push chega ao dispositivo da clínica com os alertas do turno inteiro; em modo pessoal, só as tarefas do usuário.
- API client e tipos compartilhados entre web e app num workspace único.
