# PlantãoVet: auditoria de produto e experiência

> Revisão profunda de produto, design e engenharia. O código é a fonte de verdade;
> a spec (`docs/2026-08-31-spec-plantaovet-v1.md`), a pesquisa
> (`docs/2026-08-31-pesquisa-internacao-veterinaria.md`), o glossário (`CONTEXT.md`)
> e os 28 mockups (`design/telas/`) são a intenção declarada. Onde os três divergem,
> este documento diz qual vence e por quê.

---

## 0. A tese

O produto hoje é **um sistema administrativo com nove módulos**. A ambição declarada é
**o sistema operacional da internação**. A distância entre os dois não está no CSS: está na
arquitetura da informação, que é um espelho das tabelas do banco em vez de um espelho do
turno de quem trabalha.

Três frases resumem o diagnóstico:

1. **O backend é melhor que a interface.** Regras clínicas centrais estão implementadas,
   testadas e comentadas com rigor, e inalcançáveis pela tela. Titulação de dose, dose PRN,
   execução parcial, registro retroativo, suspensão de prescrição, diária da internação e o
   alerta de evolução vencida existem no servidor e **nenhum cliente os chama**.
2. **A navegação pede que a pessoa saiba onde as coisas moram.** Nove itens de menu,
   nomeados por tabela, para um trabalho que tem exatamente dois eixos: **o tempo** e
   **o paciente**.
3. **O sistema não sabe quem está do outro lado.** Em modo estação a interface oferece
   tudo a todos; em modo pessoal, telas inteiras de gestão carregam para quem só receberá
   403 ao salvar.

O critério que este documento usa em toda decisão é o da pesquisa, não o do gosto:

> "Documentar deve ser **subproduto** da execução: 1 toque = registro auditável completo;
> chart-by-exception; **nunca pedir o mesmo dado duas vezes**." (pesquisa §4)

---

## 1. Arquitetura atual

### 1.1 Navegação (`src/front/src/App.tsx:36-113`)

| Item | Rota | Gate no menu | Gate na rota |
|---|---|---|---|
| Painel | `/` | nenhum | nenhum |
| Internados | `/pacientes` | nenhum | nenhum |
| Passagem | `/passagem` | **nenhum** | nenhum |
| Escala | `/escala` | **nenhum** | nenhum |
| Preços | `/precos` | `price_list.manage` | **nenhum** |
| Equipe | `/equipe` | `team.manage` | **nenhum** |
| Boxes | `/boxes` | `kennel.manage` | **nenhum** |
| Auditoria | `/auditoria` | `audit.read` | nenhum (backend recusa) |
| Configurações | `/configuracoes` | `clinic.configure` | **nenhum** |

Mais sete rotas sem item de menu: `/internar`, `/internacao/:id` e cinco filhas
(`/prescrever`, `/evolucao`, `/conta`, `/prontuario`, `/tutor`, `/alta`).

**18 páginas, 9 itens de menu, 0 guardas de rota.** Todo item de gestão escondido do menu
continua acessível por URL, renderiza por inteiro e só falha no `Salvar`.

### 1.2 Camadas

| Camada | O quê | Estado |
|---|---|---|
| Backend | FastAPI + SQLAlchemy async + Postgres 16, 20 routers, 58 endpoints, 21 schemas, 12 serviços, 13 migrations | maduro |
| Permissões | `app/permissions.py`, com 17 capacidades, 3 papéis, `require(cap)` em **toda** mutação | sólido nas mutações, **ausente em toda leitura** |
| Auditoria | append-only por trigger, hash encadeado (ADR-0003) | sólido; **não registra leitura** |
| Web | React 19 + Vite, sem camada de dados (nenhum cache de query), 18 páginas, `useEffect`+`fetch` cru | frágil |
| Mobile | Expo, 8 telas, cliente e tipos **copiados** do web e já divergentes | parcial |
| i18n | pt-BR + en, paridade de chaves garantida por teste | bom, com 3 vazamentos |
| Testes | 247 backend, **0 no front**, **0 e2e** | bom no back, ausente no front |

### 1.3 Entidades e o que cada uma governa

`Clinic` (tenant, locale, moeda, fuso, âncoras, perfil de compliance) → `Membership`
(papel + registro profissional + PIN) · `Kennel` · `Owner` → `Patient` →
**`Hospitalization`** (o episódio) → `Prescription` (a regra) → **`Task`** (a execução) →
`ChargeItem` (o dinheiro). Em paralelo: `ProgressNote` (evolução SOAP com adendo),
`Shift` → `ShiftNote` → `HandoverReport` → `HandoverAck`, `OwnerContact`, `AuditEntry`.

Estados: `Hospitalization` `active|discharged|died|left_ama` ·
`Task` `pending|done|partial|not_done|cancelled` + **`display_state` derivado**
(`on_time|due|overdue`) · `Prescription` `kind` `recurring|continuous|prn` com versionamento
por `replaces_prescription_id`.

**"Atrasada" nunca é persistida**: é calculada na leitura por `TaskService.display_state`,
para que painel e ficha jamais divirjam. É a promessa arquitetural mais repetida do projeto
e está honrada.

---

## 2. Personas encontradas (do código, não inventadas)

`app/permissions.py:95-99` define três papéis. O que cada um pode é fato, não opinião:

### Veterinário (`vet`): 14 das 17 capacidades
Pratica os atos privativos de quem tem registro no conselho (prescrever, ajustar, suspender,
assinar evolução, dar alta/declarar óbito) **e** toda a operação do plantão (executar tarefa,
avulsa, internar, cadastrar, contatar tutor, operar turno, abrir box), **e** lê conta e auditoria.
Não configura a clínica, não gerencia equipe, não mexe em preço.

**Seu dia** (pesquisa §1, SOP do HV-UFMS): 07h recebe o plantão · 07–08h exame físico dos
internados · 08–09h prescrições do dia · ao longo do turno, intercorrências · 16–17h ligação
aos tutores · encerra o plantão com o boletim.
**Home certa:** o plantão. **Informação crítica:** o que está fora da janela e o que mudou.

### Técnico (`tech`): 5 das 17
Executa (`task.execute`, `task.ad_hoc`), cadastra paciente, contata tutor, opera turno.
Não prescreve, não assina evolução, não interna, **não lê conta**, não lê auditoria
e, hoje, **não pode abrir um box**, embora `permissions.py:34-36` argumente exatamente o
contrário ("o vet de madrugada precisa abrir um box sem esperar o administrador"; às 3h da
manhã quem está ao lado do box é o técnico).

**Seu dia:** à beira do box, no celular ou num tablet de estação. Fila do turno → baixa de
doses → registro de eventos e notas → recebe/entrega a passagem.
**Home certa:** a fila do turno. **Informação crítica:** a próxima dose e o que atrasou.

### Administrador (`admin`): 9 das 17
Interna, cadastra, contata tutor, gerencia box, **configura a clínica, gerencia equipe e
preços, lê conta e auditoria**. Não executa tarefa clínica nem que seja dono da clínica
(`test_permissions.py:93-120` fixa isso), e **não opera turno**: não cria plantão, não fecha
plantão, não aprova nem aceita boletim.

**Seu dia:** recepção e gestão. Nunca toca o paciente.
**Home certa:** a gestão, não o painel clínico.

### O quarto principal, não modelado: **a estação**
Um dispositivo logado na clínica, compartilhado. Não tem papel (`auth.py:70-72`,
propositalmente). Quem responde pelo ato é o dono do PIN, identificado só no momento da
mutação. **A interface não sabe disso** (§3.3).

---

## 3. Matriz de permissões atual

### 3.1 O que está certo
Toda mutação autenticada passa por `require(capability)`, verificado rota a rota. Em modo
estação a checagem é feita sobre o **operador do PIN**, não sobre o aparelho
(`deps.py:127-141`), e há teste provando que um PIN de técnico numa estação não prescreve
(`test_permissions.py:123-155`). O modelo do servidor é sólido.

### 3.2 O que está errado na leitura

**Nenhuma leitura, em lugar nenhum do sistema, tem capacidade.** Aceitam um token de
estação **sem PIN nenhum**:

| Rota | Devolve |
|---|---|
| `GET /owners`, `/owners/{id}` | **`phone_e164` + `tax_id` (CPF)**, dado pessoal LGPD |
| `GET /memberships` | nome, **e-mail**, papel, registro, **`has_pin`** de toda a equipe |
| `GET /hospitalizations/{id}/charges` | o extrato inteiro, e `charges.read` é negado ao técnico e **não gateia leitura nenhuma** |
| `GET /hospitalizations/{id}/record` | o prontuário completo (documento regulado pelo CFMV) |
| `GET /clinic` | plano, limite de leitos, âncoras, `station_key_version` |
| `GET /price-list`, `/kennels`, `/patients`, `/progress-notes`, `/shifts`, `/handover/reports`, `/board`, `/tasks` | tudo |

Causa estrutural: `require()` é construído sobre `get_operator`, que **exige PIN**. Expressar
"ler o prontuário exige capacidade" com essa primitiva forçaria digitar PIN para *olhar* uma
tela, e por isso nenhuma capacidade de leitura foi escrita. Falta uma segunda primitiva.

**Consequência de esconder no front sem gatear no back** (classe de vulnerabilidade):
`/equipe`, `/configuracoes`, `/precos` e `/boxes` somem do menu de quem não tem a capacidade,
e as GETs por trás delas respondem a qualquer um. O esconde-esconde é teatro.

**Não há auditoria de leitura.** Abrir um prontuário inteiro, exportar a conta em CSV ou
listar o CPF de todos os tutores não deixa rastro nenhum, num produto cuja tese de venda é
"segurança jurídica".

### 3.3 O buraco do modo estação

```ts
// src/front/src/hooks/useSession.tsx:62-63
can: (capability) =>
  session?.kind === "station" ? true : (me?.capabilities.includes(capability) ?? false),
```

Num tablet no corredor da internação, **toda** a IA de gestão aparece: Preços, Equipe, Boxes,
Auditoria, Configurações. E as leituras funcionam de verdade, sem PIN. As escritas de equipe
e clínica são **permanentemente decorativas**: `_require_admin` recusa `kind != "personal"`,
então o botão nunca funciona, para ninguém.

Pior caso concreto: um técnico numa estação escreve uma evolução SOAP inteira, digita o PIN
no fim e recebe 403 `progress_note.sign`. **O trabalho é perdido depois da identificação.**

Não existe endpoint que responda "o que pode quem acabou de digitar este PIN". Enquanto não
existir, `can()` não tem valor honesto a devolver em estação.

### 3.4 Onde a interface oferece o impossível

| Elemento | Arquivo | Quebra para | O que acontece |
|---|---|---|---|
| Menu **Passagem** e **Escala** | `App.tsx:71-76` | admin | dois dos quatro itens primários são 100% mortos |
| ✓ / ✕ de tarefa | `TreatmentSheet.tsx:279-298` | admin | 403 `task.execute` |
| Link **Conta** | `TreatmentSheet.tsx:130` | tech | **abre**, com todo o dinheiro, e exporta CSV |
| **Adendo** em evolução assinada | `ProgressNotes.tsx:433` | tech, admin | banner aparece, formulário nunca: beco sem saída |
| Criar/encerrar plantão | `ShiftSchedule.tsx:314,415` | admin | formulário inteiro → 403 |
| Gerar/Aprovar/Aceitar boletim | `Handover.tsx:381,405,415` | admin | 403 em cada |
| `/precos`, `/equipe`, `/boxes`, `/configuracoes` | rotas sem guarda | vet, tech | página inteira renderiza; 403 só no salvar |
| Todas as telas do app | `mobile/` | admin, tech | **o mobile não tem modelo de permissão nenhum**: `Me` não traz `role` nem `capabilities` |

### 3.5 Regras que não estão na tabela
- `shift.operate` empacota **três trabalhos diferentes**: montar a escala, escrever nota de
  beira de box e assinar/receber a passagem. Por isso um técnico monta a escala da clínica e
  um administrador não consegue fechar um plantão. Os dois ao contrário.
- `charges.read` gateia um **POST** (`charges.py:50`) e nenhuma leitura.
- `handover ack` não confere se quem aceita é o receptor do turno (`handover.py:155-170`).
- `hospitalizations.py:54` aceita qualquer membership como `vet_membership_id`, e um técnico
  pode ser gravado como veterinário responsável pela internação. O filtro é só client-side.
- `Membership.permissions` (jsonb) nunca é lido nem escrito: schema morto.
- `AUDIT_READ` existe como constante e **nenhuma rota a usa**; `audit.py:42` faz
  `if role == "tech"` inline, exatamente o que o docstring de `permissions.py:3` proíbe.

---

## 4. Jornadas atuais

Medidas em navegações de página e campos digitados.

| # | Jornada | Hoje | Atrito principal |
|---|---|---|---|
| A | Internar paciente novo | `/pacientes` → busca → "Cadastrar novo" → `/internar` (563 linhas de formulário) | ok no cadastro em um passo; a admissão pede consentimento, box e vet responsável que o sistema poderia inferir |
| B | Internar paciente existente | idem | `?paciente=<id>` falha em silêncio → formulário vazio sem explicação (`Admission.tsx:169`) |
| C | **Abrir o sistema e saber o que fazer** | `/` → 4 KPIs → lista **ordenada por nome do paciente** | **o cartão vermelho pode ser o último da lista**; nenhum sinal de turno, de passagem pendente ou do que mudou desde a última olhada |
| D | Executar medicação (web) | `/` → paciente → ficha → achar o chip → ✓ | sem eixo de horas: acha-se o chip por varredura visual |
| D' | Executar medicação (app) | fila → tarefa → ✓ | melhor que o web |
| E | Registrar não realizada | ✓/✕ → diálogo → motivo | ok |
| F | **Registrar dose PRN** | **impossível no web** | `api.adHocTask` tem 0 chamadas; a prescrição PRN aparece na ficha sem horário nenhum e sem botão |
| G | Perceber atraso | esperar o polling de 5s e varrer a lista | nenhuma ordenação por urgência, nenhum agrupamento por tempo |
| H | Intercorrência | só pelo app (`EventScreen`) | no web não existe; o switch "avisar o veterinário" **não avisa ninguém** |
| I | Trocar paciente de box | não existe ação | `Kennels.tsx` cruza box e paciente **por string do nome** |
| J | **Escrever a passagem** | `/escala` → encerrar turno → `/passagem` → *por paciente*: "Gerar resumo" → "Aprovar" | dois módulos para uma cerimônia; o resumo não está pronto quando se chega; não existe "Concluir passagem" |
| K | **Receber a passagem** | `/passagem` → "Aceitar" por paciente | **as pendências não aparecem no ato do aceite**, só contadores. É o elemento nº 1 do I-PASS, e é o que falta |
| L | Dar alta | ficha → `/alta` | o front conta **todas** as pendentes; o backend só as vencidas → confirmação que aparece sempre |
| M | Configurar a operação | `/configuracoes` (652 linhas) | tolerâncias e cerimônias exibidas como configuração e **não configuram nada** |
| N | Cadastrar equipe | `/equipe` | a tabela de permissões mostrada ao admin **está errada** sobre o que o admin pode |
| O | Gerenciar boxes | `/boxes` | box desativado some para sempre (`includeInactive` nunca é passado) |
| P | Investigar na auditoria | `/auditoria` + 16 chips de filtro | investigação começa numa entidade, não num módulo; não há "ver trilha" no paciente nem na prescrição |
| Q | Prescrever | ficha → `/prescrever` | o preview do aprazamento usa âncoras **hardcoded** no front e ignora `clinic.anchors` que a própria tela de Configurações deixa editar |
| R | **Ajustar dose (titulação)** | **impossível** | endpoint completo, auditado e versionado; **nenhum cliente o chama** |
| S | Escrever evolução | ficha → `/evolucao` | contadores "desde a última evolução" calculados sobre a janela de ±12h → subcontagem sistemática |
| T | Contatar tutor | ficha → `/tutor` | **não há como registrar o opt-in de WhatsApp** → o caminho vive travado em 409 |
| U | Ver a conta | ficha → `/conta` | "executado e não cobrado" calculado sobre ±12h → errado em toda internação de mais de um dia |

**Padrão que se repete:** a ficha do paciente tem seis botões que **navegam para fora** dela.
Evolução, conta, tutor, prontuário, alta e prescrição são páginas inteiras, e voltar significa
perder o contexto e recarregar tudo.

---

## 5. Problemas encontrados

### 5.1 Bloqueadores de segurança clínica

1. **O único job do sistema nunca roda.** `build_scheduler` (`workers/scheduler.py:49`) não é
   invocado em lugar nenhum. As tarefas só existem pela janela de 48h criada no momento da
   prescrição. **Uma internação de mais de dois dias esvazia a ficha sozinha**, literalmente
   o "patients randomly fall off my board" que a pesquisa (§6) aponta como dano irrecuperável
   de confiança.
2. **O painel mente no número que a enfermaria lê de relance.** `board.done` exibe
   "{{done}} de {{total}} feitas" alimentado por contadores que só contam **pendentes**
   (`schemas/board.py:8-11`). Diz "9 de 12 feitas" quando nada foi feito.
3. **`on_time_rate` não é aderência.** É a fração das pendentes que ainda não venceu; sobe
   quando se prescreve mais para o futuro; marca 100% numa ala que não executou nada.
4. **O aceite da passagem não sobrevive ao reload.** O front guarda o aceite em `useState`
   local por acreditar que a API não o devolve, **e ela devolve** (`schemas/handover.py:29-32`,
   preenchido por `_enrich`). Recarregar zera a barra de progresso da feature-herói. O mesmo
   bug está reimplementado no mobile.
5. **As pendências não são visíveis no ato do aceite.** Só contadores. A spec é literal:
   "aceite explícito paciente a paciente, com pendências e atrasadas visíveis **no próprio ato**".
6. **O botão de administrar dose fora da janela, no app, é revelado farejando se a mensagem
   de erro traduzida contém `"?"`** (`TaskScreen.tsx:90`).
7. **`notify_vet` não avisa ninguém.** O técnico registra convulsão às 3h marcando "avisar o
   veterinário" e nada acontece: nenhuma rota lê o campo.
8. **O boletim de WhatsApp grava "enviado" no prontuário sem enviar nada**
   (`whatsapp.py:21-27` devolve `stub-<uuid>`; a rota grava `sent_at` e o front diz "enviado").

### 5.2 Bloqueadores de coerência de produto

9. **A tela-herói não foi construída como especificada.** `CONTEXT.md` define TreatmentSheet
   como "a grade hora × tarefa, **agrupada por categoria**". A implementação é uma lista
   vertical de prescrições com uma fita de chips: **sem eixo de tempo e sem categoria**: os
   dois eixos organizadores da prancheta de papel.
10. **Cinco conceitos centrais do domínio são inalcançáveis:** titulação (`adjust`),
    suspensão, dose PRN, execução parcial e registro retroativo. Todos completos e testados
    no servidor.
11. **A diária da internação nunca é lançada.** `accrue_daily_rates` só é chamado pelos testes.
    É a maior linha da conta.
12. **O alerta "internado sem evolução há 24h"**, obrigação do CFMV virada em produto, era
    calculado no servidor e não aparecia em tela nenhuma.

### 5.3 Erros engolidos (o usuário vê o dado errado, nunca o erro)

18 `catch` silenciosos. Os que causam decisão errada:

| Onde | Quando falha, a pessoa vê |
|---|---|
| `PatientsList.tsx:111` | "Nenhum resultado" → a recepção conclui que o paciente não existe e **cadastra duplicado** |
| `Discharge.tsx:56` | total da conta **R$ 0,00** → o vet dá alta achando que não há o que cobrar |
| `OwnerContacts.tsx:85` | tutor "desconhecido" e botão de enviar boletim **habilitado sem confirmação de consentimento** |
| `useSession.tsx:51` | `/auth/me` falha → `can()` devolve `false` para tudo → **todos os botões somem** e a tela parece só "vazia" |
| `PriceList.tsx:104` | preços formatados em **BRL** numa clínica USD/EUR |

**Nenhum dos dois clientes trata 401 / `token_expired`.** A chave de tradução diz "Entre de
novo" e não existe caminho: a pessoa fica presa numa tela com sessão morta.

### 5.4 Escala e correção
- `GET /shifts` sem `from`/`to`: depois de ~2 meses a escala mostra os **50 turnos mais antigos**
  e não há paginação possível (`next_cursor` é `None` fixo).
- `OwnerContacts` baixa `patients()` e `owners()` inteiros (limite 50) e faz `.find()` no
  cliente: clínica com 51 pacientes perde o nome do tutor em silêncio.
- `Kennels` cruza paciente e box **por igualdade de string do nome**: dois boxes homônimos, ou
  renomear um box ocupado, põe o paciente no box errado.
- `timezone` é campo de texto livre validado só por `min_length=1`. Um fuso inválido faz
  `ZoneInfo()` estourar em `SchedulingService.generate` → **500 em toda prescrição**.
- `Charges` agrupa "hoje" pelo dia do **navegador**; o extrato agrupa pelo dia da **clínica**.

---

## 6. Duplicações

| Conceito | Implementado em | Consequência |
|---|---|---|
| **Aprazamento** | `services/scheduling.py` **e** `NewPrescription.tsx:90-126` com âncoras **hardcoded** | o preview mostra horários que o servidor não vai criar quando a clínica edita as âncoras |
| **Tolerância ISMP** | 4 lugares (schema, 2 telas, rota de avulsa) | uma mudança exige quatro edições |
| **Frequências/âncoras padrão** | 4 tabelas | frequência criada em Configurações não aparece em Nova Prescrição |
| **Contadores do boletim** | serviço + web + mobile | três aritméticas para o mesmo número |
| **Cliente HTTP e tipos** | `front/api/*` **copiado** em `mobile/api/*` | já divergiram: `ChargeDay.day` vs `.date`; o `Me` do mobile não tem `role` nem `capabilities` |
| **Formatação de dinheiro** | 4 lugares, com `"BRL"` hardcoded em 5 | a moeda da clínica é ignorada |
| **Registro profissional (CRMV)** | 4 formatadores | |
| **`initials()`** | 2 arquivos, com fallbacks diferentes (`"··"` vs `"?"`) | |
| **Card de estatística** | `Board.Stat` e `Kennels.Counter` idênticos byte a byte, mais uma variante inline em `Charges` | |
| **Painel × Internados** | as duas telas renderizam **o mesmo `api.board()`** com os mesmos contadores, sob dois itens de menu | duas casas para um conceito |
| **`_require_admin`** | 3 cópias | |
| **Dobra de acentos** | 3 implementações | |

### Vocabulário: divergências do `CONTEXT.md`
`billing` (evitado, é `ChargeItem`) em toda a folha de estilo e em 15 usos ·
`catalog` (evitado, é `PriceListItem`) no código **e na string de UI** ·
`duty` (evitado, é `Shift`) · `slot` (evitado, é `Bed`) · `tenant` (evitado, é `Clinic`) ·
`guardian` (evitado, é `Owner`) · `receitas` no seed (evitado, é `Prescription`) ·
identificadores em português em 15 arquivos do backend, contra o ADR-0004.

**O idioma da interface vem de `navigator.language`, não de `clinic.locale`**, enquanto o
backend gera conteúdo no locale da clínica. Uma clínica `en` vê a UI em português e as
cerimônias em inglês.

---

## 7. Funcionalidades incompletas

Classificação: **completar · integrar · consolidar · mover · esconder · remover**.

### Integrar (existe no servidor, falta o caminho na tela)
| O quê | Disposição |
|---|---|
| `build_scheduler`, o único job | **integrar** (bloqueador) |
| `accrue_daily_rates`, a diária | **integrar** |
| `POST /prescriptions/{id}/adjust`, titulação | **integrar** |
| `POST /prescriptions/{id}/suspend` | **integrar** |
| `POST /tasks/ad-hoc` com `prescription_id`, dose PRN | **integrar** |
| `TaskExecute.partial` + `values.dose_given` | **integrar** |
| `TaskExecute.retroactive` + `performed_at` | **integrar** |
| `GET /compliance/alerts`, evolução vencida | **integrado durante esta auditoria** |
| `GET /memberships/roster`, lista sem e-mail nem PIN | **integrar** (fecha o vazamento de e-mails) |
| `HandoverReportOut.acked_at` / `patient_name` | **integrar** (o cliente reimplementa o bug que o servidor preveniu) |
| `api.setWhatsAppOptIn` | **integrar** (sem ele o WhatsApp é inalcançável) |

### Completar
Transcrição de áudio (`transcribe()` devolve `""`) · waveform fabricada com `Math.sin` ·
`notify_vet` · envio real de WhatsApp · push do servidor (o token do Expo é obtido e
**descartado**) · `scheduleCriticalReminder` (exportado, 0 chamadas) · verificação da cadeia
de hash (`prev_hash` não é devolvido) · "Baixar PDF" que é `window.print()` ·
sinais vitais como dado de primeira classe · jejum bloqueando `nutrition` ·
contador de dose por fármaco.

### Esconder ou remover
`DEFAULT_TOLERANCES` renderizado como configuração que não configura ·
`delivered_at`/`read_at` de WhatsApp (nunca chegam) ·
14 chaves i18n mortas · 13 interfaces mortas no `types.ts` do mobile (~250 de 315 linhas) ·
`descending` em `pagination.py` · o `try/except ImportError` de `records.py` ·
`createOwner`/`updateOwner`/`createPatient` (substituídos por `registerPatient`).

### Mover
`br_human` (perfil de **saúde humana**) é escolhível em produção hoje, com o próprio arquivo
admitindo que "precisa de revisão jurídica". A spec diz "perfil por **país**". **Esconder até
haver cliente e revisão.**

---

## 8. Rotas e telas candidatas a deixar de existir

| Hoje | Proposta | Por quê |
|---|---|---|
| `/pacientes` **e** `/` | **uma** tela de censo + um **modo** de painel | renderizam a mesma fonte com dois layouts sob dois nomes |
| `/escala` | dividida: *meu plantão* vai para a home; *a escala* vai para Equipe | montar escala é gestão; assumir/encerrar plantão é operação |
| `/boxes` | vira **uma visão** de Internados (Lista ⇄ Boxes) | ocupação é um jeito de olhar o censo, não um módulo |
| `/auditoria` | **uma lente**, não um módulo: "ver trilha" no paciente, na prescrição e na pessoa; a página global fica na Gestão | investigação começa numa entidade |
| `/internacao/:id/{evolucao,conta,prontuario,tutor}` | **abas** dentro do paciente | quatro navegações que perdem contexto |
| `/precos`, `/equipe`, `/configuracoes` | agrupadas em **Gestão** | não competem com paciente e tarefa |
| `/passagem` | continua existindo, **deixa de ser item de menu** | chega-se por ela pelo turno, não por um módulo |

---

## 9. Arquitetura da informação proposta

**Princípio:** o produto tem dois eixos: **o tempo** e **o paciente**. Tudo o mais é lente,
aba ou ação contextual.

### Navegação: de 9 itens para 2–3, dependendo de quem entra

| Item | Rota | vet | tech | admin |
|---|---|:--:|:--:|:--:|
| **Plantão**, o que precisa de mim agora | `/` | ✅ home | ✅ home | não |
| **Internados**, o censo (lista ⇄ boxes) | `/internados` | ✅ | ✅ | ✅ home |
| **Gestão**: equipe · escala · preços · auditoria · configurações | `/gestao/*` | não | não | ✅ |

O administrador não vê Plantão: não executa tarefa e não opera turno. O técnico não vê
Gestão: não tem nenhuma das capacidades. Ninguém vê um menu que não pode usar.

**Destinos (sem item de menu):** `/internacao/:id` com abas · `/internar` · `/passagem`
(alcançada pelo turno) · `/painel` (o mesmo censo em tela cheia, para pendurar na parede).

### O paciente é um lugar, não seis páginas

`/internacao/:id` com abas (**Ficha · Evolução · Conta · Prontuário · Tutor**) e um
cabeçalho de contexto sempre presente: nome, espécie, peso, box, dias internado, vet
responsável, e os selos que mudam decisão (crítico, jejum, evolução de hoje registrada).
Prescrever, ajustar, suspender, dar alta e registrar PRN são **ações no contexto**, não
destinos.

### Gestão tem uma porta só
`/gestao` com sub-navegação própria: **Equipe & Escala · Preços · Auditoria · Configurações**.
Sai do caminho de quem cuida de paciente.

---

## 10. Jornada proposta

**Chego para o plantão.** Abro o sistema. A primeira linha diz de quem é o turno e até quando.
Se há passagem para receber, é a única coisa em destaque: **recebo paciente a paciente, com as
pendências e as atrasadas escritas ali**, não contadas, escritas. Aceito.

**Trabalho.** A home mostra, nesta ordem: **o que precisa de atenção agora**, com o motivo em
palavras ("Nina · UTI 02 · glicemia atrasada há 2h10 · crítica"), ordenado do pior para o
melhor; depois a fila em **AGORA · PRÓXIMA HORA · DEPOIS**. Dou baixa na própria linha.
Quando não há exceção, a tela diz isso e desaparece. É chart-by-exception.

**Cuido de um paciente.** Um clique leva à ficha: a grade hora × tarefa agrupada por
categoria, com a linha do "agora". Prescrevo, ajusto a taxa, registro um PRN, escrevo a
evolução, sem sair do paciente.

**Entrego o plantão.** A home me oferece encerrar. O boletim **já está escrito** a partir do
que aconteceu; eu reviso e complemento. Aprovo. Concluo a passagem.

---

## 11. Navegação proposta: o que muda em cada item

| Hoje | Vira | Motivo |
|---|---|---|
| Painel | **Plantão** (`/`), centro de decisão | responde "o que precisa de mim agora", não "quantos KPIs cabem" |
| Internados | **Internados** com visão Lista ⇄ Boxes | uma casa para o censo |
| Passagem | **momento**, não módulo: receber no início, entregar no fim | a passagem usa o que o sistema já sabe |
| Escala | *meu plantão* na home · *a escala* em Gestão → Equipe | dois trabalhos diferentes |
| Preços | Gestão | administração |
| Equipe | Gestão (com a escala junto) | administração |
| Boxes | visão de Internados + ações contextuais | ocupação é lente do censo |
| Auditoria | lente contextual + página em Gestão | investigação começa na entidade |
| Configurações | Gestão | administração |

---

## 12. Componentes e padrões a consolidar

Hoje cada página inventa seu próprio cabeçalho e há estilo inline em toda parte
(`ui.tsx` exporta 6 primitivas; 18 páginas desenham botão, card, tabela, selo e estado vazio
à mão). A consolidar:

- **Chrome de página**: `Page` (título, subtítulo, ações), `Section`, `Toolbar`.
- **Estados**: `EmptyState`, `Skeleton`, `ErrorState` com ação de repetir. Hoje o
  carregamento é `<p>Carregando…</p>` e o vazio é um `Card` com texto cinza.
- **Feedback**: um único mecanismo de confirmação e de erro. Hoje há `window.confirm` na
  ficha, `ErrorBanner` em algumas páginas e silêncio em outras.
- **Primitivas**: `Stat`, `Badge`, `DataTable`, `Dialog`, `Money`, `License`, `RelativeTime`.
- **Autorização**: um só lugar, com `can()` honesto (inclusive em estação), `<Gate>` para
  elementos e guarda de rota, para acabar com o "renderiza e depois 403".
- **Tokens**: mover o estilo inline para classes; a fonte da identidade
  (Bricolage Grotesque + Instrument Sans) **não está carregada**, e as 10 declarações
  `fontFamily` caem em Segoe UI.

---

## 13. Alterações de backend necessárias

1. **Ligar o `build_scheduler`** no ciclo de vida da aplicação. *(bloqueador)*
2. **Primitiva de leitura autorizada** (`require_read`) + capacidades de leitura para conta,
   prontuário, tutor (PII), equipe e clínica.
3. **Endpoint de operador**: o que pode quem digitou este PIN. Sem isso a estação não tem
   como esconder o impossível.
4. **`GET /shift/now`**: turno atual, passagem pendente, exceções ordenadas por urgência com
   motivo, fila em AGORA/PRÓXIMA HORA/DEPOIS. Uma ida ao servidor para a home.
5. **Ordenar o board por urgência**, não por nome do paciente.
6. **Corrigir os contadores do board** (hoje contam só pendentes e são rotulados "feitas").
7. **Boletim com as pendências dentro**: a lista, não o número.
8. **Diária**: chamar `accrue_daily_rates` (no fechamento do dia e na alta).
9. **Envelope de lista único**. Hoje há `T[] | Page<T>` e um normalizador `asList` no cliente.
10. **Validar `timezone`** e o papel do `vet_membership_id` na admissão.
11. **Dividir `shift.operate`** em escalar / anotar / aprovar / aceitar.
12. **Auditar leitura** de prontuário e de conta.

---

## 14. Alterações de frontend necessárias

1. Novo shell e nova navegação por papel, com **guarda de rota** (fim do "renderiza e 403").
2. `can()` honesto em modo estação.
3. Home "Plantão" orientada a exceção e a tempo.
4. Paciente como contexto com abas.
5. Ficha como **grade hora × tarefa agrupada por categoria**, com linha do "agora", iniciais e
   horário real na célula, motivo do não realizado, e a linha PRN com contador de dose.
6. Passagem integrada ao turno; aceite lido do servidor; pendências visíveis no ato.
7. Titulação, suspensão, PRN, parcial e retroativo com caminho na tela.
8. Erros: nada de `catch` silencioso; tratamento de 401; um só mecanismo de feedback.
9. Sistema de componentes e tokens; carregar as fontes.
10. Locale vindo de `clinic.locale`.
11. Mobile: `role`/`capabilities` no `Me`, esconder o que não pode, e ler o aceite do servidor.

---

## 15. Riscos de regressão

| Risco | Mitigação |
|---|---|
| 247 testes de backend fixam caminhos de rota e formatos de resposta | **aditivo primeiro**: novos campos e rotas antes de remover os antigos; rodar a suíte a cada etapa |
| Rotas antigas em favoritos e links | `Navigate` de toda rota antiga para a nova |
| Regra clínica escondida num `catch` | ler o serviço antes de mexer; as regras estão comentadas com o porquê |
| Teste de paridade de i18n quebra por chave só em pt-BR | toda chave nova entra nos dois catálogos |
| Gatear leituras pode quebrar telas que hoje leem tudo | introduzir a capacidade com o papel certo e um teste por rota |
| **A suíte era intermitente**: 4 a 6 testes diferentes falhavam a cada execução | **corrigido**: `migrated_database` reinstanciava por event loop e derrubava o banco no meio da suíte; guarda de processo em `tests/conftest.py`. De 4–6 falhas aleatórias para **247 passando** |

---

## 16. Plano de execução

Etapas coerentes, sistema funcionando ao fim de cada uma.

| # | Etapa | Entrega |
|---|---|---|
| 0 | **Rede de segurança** ✅ | suíte estável (247 verdes), baseline de lint e typecheck |
| 1 | **Verdade e segurança no servidor** | job ligado; leituras gateadas; endpoint do operador; board ordenado por urgência e com contadores corretos; `/shift/now`; boletim com pendências |
| 2 | **Sistema de design** | chrome de página, estados, feedback, autorização declarativa, tokens e fontes |
| 3 | **Nova arquitetura da informação** | shell por papel, guardas de rota, redirecionamentos, paciente com abas, Gestão |
| 4 | **A home "Plantão"** | exceção primeiro, tempo em AGORA/PRÓXIMA HORA/DEPOIS, ação na própria linha |
| 5 | **A ficha como grade** | hora × tarefa por categoria, linha do agora, PRN, parcial, retroativo, titulação |
| 6 | **Passagem integrada** | receber no início, entregar no fim, pendências no ato do aceite |
| 7 | **Validação** | jornadas, testes, lint, typecheck, build |

---

## 17. O que foi entregue

### Rede de segurança
A suíte falhava em 4 a 6 testes **diferentes a cada execução**. Parecia bug de
produto e era o harness: `migrated_database` era reinstanciado quando o event
loop mudava e derrubava o banco no meio da suíte; e execuções paralelas
brigavam pelo mesmo `plantaovet_test`. Guarda de processo + um banco por
processo. **De 4–6 falhas aleatórias para 415 testes verdes.**

### Verdade e segurança no servidor
| | |
|---|---|
| O único job do sistema nunca rodava | ligado no `lifespan`, com catch-up na subida; a janela de tarefas volta a ser estendida |
| A diária nunca era lançada | entra no job de hora em hora, idempotente por dia |
| Toda leitura era aberta | primitiva `require_read`: tutor (CPF/telefone), conta, prontuário, equipe e configurações exigem alguém identificado; a operação do plantão segue aberta, que é para isso que a estação existe |
| A estação oferecia tudo a todos | `GET /auth/operator` responde o que pode quem digitou o PIN |
| Leitura do prontuário sem rastro | `record_read` na trilha |
| Painel ordenado por nome | ordenado por urgência, com o motivo e a magnitude |
| "N de M feitas" contava pendentes | `done_today`/`planned_today` contam execuções no dia da clínica |
| Fuso do aparelho | `/clinic/profile` carrega fuso, moeda e locale; todo formatador passa por `useClinic` |
| Aprazamento duplicado no cliente | `POST /prescriptions/preview`: uma regra, um lugar |
| Âncora da cerimônia gravada e nunca lida | a âncora da prescrição vence a da clínica |
| Fuso inválido derrubava toda prescrição | validado na entrada |
| `shift.operate` misturava três trabalhos | `shift.schedule` separado: o admin monta a escala, o técnico não |
| `kennel.manage` negado ao técnico | concedido, porque às 3h quem está ao lado do box é ele |
| Alta era beco sem saída | `GET /hospitalizations?patient_id=`; a alta leva à conta da internação encerrada |

### As integrações
- **WhatsApp**: Meta Cloud API real, com template aprovado, assinatura
  `X-Hub-Signature-256` no webhook e status de entrega. **E parou de mentir:**
  sem credencial a tentativa é gravada como `failed` com `sent_at` nulo. O teste
  que exigia 201 e `external_id` era a garantia da mentira; foi reescrito.
- **IA multi-provedor**: `AI_TEXT_PROVIDER=vertex|openai|anthropic|stub` e
  `AI_SPEECH_PROVIDER=openai|stub`. Nome inválido falha na subida. O prompt sai
  de uma *allowlist*: campo novo no esqueleto não vaza dado de tutor por
  omissão. Provedor fora do ar cai no texto determinístico e a passagem segue.
- **Push por FCM**: registro de aparelho, e o **orçamento de alertas**: só dose
  crítica fora da janela e intercorrência com "avisar o veterinário", com teto
  por hora e sem repetir a mesma dose. O switch `notify_vet` do app, que não
  avisava ninguém, agora avisa quem está de plantão.
- **PDF real**: gerado no servidor com timbre (endereço, telefone, CNPJ, três
  campos que a tela lia e nunca existiram), paginação e assinatura por evolução.
- **Sinais vitais e jejum**: vitais com faixa de referência por espécie; jejum
  avisa e exige override auditado, nunca bloqueia.
- **Contador de dose por fármaco**: o gap que a pesquisa aponta como não
  resolvido por nenhum concorrente.

### A calculadora de dose
Não é uma tela: desaparece dentro do prescrever. Escolhido o item, o campo já
vem preenchido e **a conta fica visível**. O que o veterinário confere não é o
número, é o caminho. Posologia por (apresentação, espécie), porque cão e gato
metabolizam diferente; dose fixa por animal quando é o caso, porque
multiplicá-la pelo peso *é* o erro; raça entra como aviso (ABCB1-1∆/MDR1,
galgos). **Regra sem revisão de veterinário não pré-preenche nada**, e mexer no
número derruba a conferência anterior.

### Configuração: o que a clínica passou a mandar

Três coisas apareciam na tela como fato consumado e viraram decisão da clínica.

- **Onde a posologia se cadastra.** A calculadora existia e o formulário dela
  não: as regras da demo tinham sido inseridas direto no banco. Agora a
  posologia mora **dentro do item da tabela de preços**, ao lado do preço e da
  concentração, porque é lá que o fármaco já é definido uma vez. Uma tela
  separada de "posologias" seria um segundo cadastro do mesmo remédio, e a
  divergência entre os dois é questão de tempo. O diálogo virou duas colunas: à
  esquerda o item, à direita a posologia, cada uma com começo, meio e fim.
  Empilhadas, o "Salvar" do item caía no meio da tela com outra seção inteira
  abaixo, e o fim do trabalho parecia um passo do meio.
- **Tolerância de atraso.** Eram três constantes no código, exibidas sob a
  legenda *"Só leitura: nada aqui é configurável pela clínica hoje"*. As janelas
  ISMP (30/60/120) continuam sendo o default, e viraram o ponto de partida: uma
  UTI com bomba de infusão e um hotelzinho de pós-operatório não têm o mesmo
  conceito de atraso. Como "atrasada" é DERIVADA na leitura, mudar a janela muda
  o mural, a fila e a passagem no mesmo instante, inclusive para o que já está
  prescrito, e a tela diz isso.
- **Cerimônias da admissão.** Liga, desliga e muda o horário. O catálogo do que
  o produto conhece vive no cliente porque o modelo guarda só as ligadas: sem
  ele, desligar uma cerimônia seria uma porta de mão única.

### Aparelho no lugar de chave, e o PIN que trava

A chave de estação era **uma senha para a clínica inteira**. Três coisas
quebravam com ela, e nenhuma quebra mais:

1. **Revogar era tudo ou nada.** Um tablet sumia e a saída era trocar a chave,
   derrubando todos os outros aparelhos ao mesmo tempo, no meio do plantão.
   Agora cada aparelho tem segredo próprio; revogar um não toca nos demais.
2. **Ninguém sabia quais aparelhos existiam.** A chave era um texto que
   circulava. Agora há lista, nome ("Tablet da UTI") e "visto pela última vez",
   que é a única informação que faz alguém decidir revogar um.
3. **O bloqueio por erro de PIN não durava nada.** Vivia na memória do processo,
   chaveado por um identificador sorteado a cada login: relogar zerava a
   contagem, e um restart da API também. Agora o aparelho é a identidade, o
   bloqueio fica no banco, e **sair dele é ato de um administrador**, não
   passagem do tempo: cinco PINs errados seguidos são alguém tentando adivinhar,
   e um cronômetro só faz essa pessoa esperar.

A liberação é um código de seis dígitos que o administrador gera e o aparelho
digita; vale cinco minutos e morre no uso. Depois disso o tablet volta a
funcionar sozinho quando é ligado. A chave única continua aceita, embaixo e
rotulada como modelo antigo, porque há aparelho em campo que só conhece ela:
derrubar todos de uma vez para estrear um modelo de acesso seria a mesma falha
que o modelo novo corrige.

O **PIN passou a seis dígitos**. Com quatro são dez mil combinações, e o PIN é
único por clínica porque dois iguais atribuiriam o ato clínico à pessoa errada:
em algumas centenas de pessoas a colisão deixa de ser exceção e vira o caso
comum. Quem já tem um PIN de quatro continua entrando com ele até trocar. E
**cada um troca o próprio PIN**: existia só o caminho do administrador definir o
de outra pessoa, então trocar um PIN que alguém viu por cima do ombro dependia
de pedir a um terceiro, e o incentivo era não trocar.

Uma ressalva honesta sobre o pedido original: bloquear *o usuário* depois de
cinco erros não é possível enquanto o PIN é a própria identificação. Um PIN que
não casa com ninguém não pertence a ninguém, e não há quem bloquear. O que o
sistema consegue identificar é o **aparelho**, e é ele que trava.

### A plataforma: quem vende e dá suporte

Não existia ninguém "do lado de fora". Todas as rotas são escopadas por
clínica e, por construção, ninguém enxergava mais de uma: vender e dar
suporte era impossível sem entrar no banco. Agora há uma segunda porta,
`/plataforma`, com token próprio (`kind=platform`) que **nenhuma rota de
clínica aceita**, e que só as rotas `/platform/*` leem. As duas portas são
disjuntas por tipo, não por filtro.

- **A lista responde as três perguntas de quem vende**: tem gente? tem
  paciente? ainda usa? A terceira separa o cliente ativo do que vai cancelar,
  e vem da trilha de auditoria da clínica, que é o único registro honesto de
  uso. A ordem é a do trabalho: em atraso primeiro, teste acabando depois,
  quem parou de usar em seguida.
- **Onboarding num ato**: clínica e primeiro administrador. A senha é sorteada
  legível ao telefone (sem 0/O, 1/l, em grupos) e aparece uma vez; no banco
  fica o hash. Trocar de plano preenche o limite do plano.
- **Suspender fecha a porta no login, nunca no meio da sessão.** Uma clínica
  com paciente internado não perde a prescrição por causa de boleto: quem já
  está de plantão termina o turno, quem chega depois vê o motivo
  (`clinic_suspended`), e não "credencial inválida".
- **Suporte sem porta dos fundos.** Não há "entrar como". As duas ligações mais
  comuns (senha e PIN) têm um botão cada, e tudo o que o suporte faz fica na
  trilha da clínica com o prefixo "Suporte PlantãoVet · nome". O cliente vê que
  o suporte mexeu, e vê o quê. O suporte nunca escolhe o PIN de ninguém: zera,
  e a pessoa define o próprio.
- **O cliente vê a assinatura sem ver o comercial**: o perfil da clínica expõe
  só status e data, e o administrador (só ele) recebe uma linha no topo quando
  o teste está acabando ou há mensalidade em atraso.

### Planos como dado, e o limite de leitos fora do alcance da clínica

O administrador da clínica podia subir o próprio limite de leitos, que é a
unidade de cobrança. `bed_limit` saiu de `ClinicUpdate` e o campo passou a ser
recusado com 422 (`extra="forbid"`), não ignorado: campo ignorado em silêncio
parece campo aceito. Na tela, plano e limite viraram leitura.

Os planos deixaram de ser um dicionário no código. Quem vende cria, edita,
aposenta e migra planos em `/plataforma/planos`, sem deploy:

- **Um plano de teste é um plano com dias de teste.** Quem entra nele começa em
  `trial` com a data de fim já calculada. Um plano pago não encerra teste
  nenhum por conta própria: um teste de 30 dias do Pro é decisão comercial, e
  é quem vende que marca `active` quando o pagamento chega.
- **"Fundador" não é um tipo especial.** É um plano com preço de lançamento
  que um dia se aposenta: ninguém novo entra, quem está fica. **Migrar** move
  todas as clínicas dele para o definitivo num ato, com o limite do plano novo
  e uma entrada na trilha de cada clínica dizendo de onde veio e para onde foi.
- Mudar limite ou preço de um plano não mexe em quem já está nele: o que cada
  clínica tem foi combinado na hora. Migrar é o ato que reaplica.
- Apagar só plano vazio. Com clínica dentro, o caminho é migrar.

### Web primeiro: push no navegador e o site na tela inicial do celular

O web sai antes do app da loja, e o técnico vai abrir isto no telefone.

- **Push no navegador pelo MESMO caminho do app.** O navegador é só mais um
  token na tabela de aparelhos, com `platform="web"`; o backend ganhou o bloco
  `webpush` na mensagem do FCM (ícone, urgência alta, `requireInteraction`, e
  o link que abre a ficha do paciente ao tocar). Nada de segundo provedor: o
  mesmo orçamento de alertas e a mesma varredura de dose crítica chegam no
  Chrome de quem está de plantão. Só na sessão pessoal: a estação é um tablet
  compartilhado, e o alerta é da pessoa, não do aparelho do corredor.
- **A permissão nunca é pedida ao abrir.** Há um botão "Receber alertas aqui"
  ao lado de quem está logado, que diz o que vai chegar. Sem a configuração
  do Firebase no build (`VITE_FIREBASE_CONFIG`, `VITE_FIREBASE_VAPID_KEY`), o
  botão não existe: botão que não consegue receber é pior que nenhum.
- **Instalável.** Manifest, ícones (gerados sem dependência), `theme-color`,
  as três linhas que o iOS lê no lugar do manifest, e um cabeçalho que no
  celular vira duas linhas com a navegação rolando de lado. O service worker
  não guarda cache: offline degradado está fora do escopo, e um cache ali
  viraria uma segunda fonte de verdade para a ficha do paciente. O nginx serve
  o worker e o manifest com `no-cache`: uma versão nova precisa chegar na
  próxima abertura, senão o push roda código velho com a aba fechada.

### Ainda em aberto
- Mensagens recebidas do tutor pelo WhatsApp: precisa de credencial por clínica
  e de um lugar para a mensagem sem internação associada.
- Orçamento de alertas é estado de processo: com mais de um worker, o teto dobra.
- Offline degradado e biometria continuam fora.
- As faixas de referência marcadas `needs_vet_review` e as posologias da demo
  precisam da sessão de 30 min com o veterinário (spec §8.1).
