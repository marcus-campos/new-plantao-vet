# PlantãoVet v1 — Especificação do Produto

> Consolidada em 2026-08-31 após sessão de design (grilling) + pesquisa de mercado + **revisão adversarial em 3 lentes** (clínica, engenharia, compliance — 30 achados, 27 acatados; ver §9) + **decisão de i18n nativo** (ADR-0004).
> Glossário bilíngue canônico: `CONTEXT.md`. Pesquisa: `docs/2026-08-31-pesquisa-internacao-veterinaria.md`. ADRs: `docs/adr/`.

## 1. Visão

**A ficha de internação digital que faz o plantão passar direito.** Substitui a prancheta de papel da internação; o sistema que a clínica já usa continua no financeiro/histórico geral. Web desktop para ficha, painel e gestão; app móvel companion (Android/iOS) para o plantonista.

**Três diferenciais** (nenhum concorrente tem — ver pesquisa §2):
1. Passagem de plantão estruturada (I-PASS) com boletim rascunhado por IA e aceite do receptor.
2. Compliance automático com auditoria imutável ("segurança jurídica") — perfil por país, `br` na v1.
3. Disciplina anti-ruído: janelas ISMP por criticidade, orçamento de alertas.

**Preço público (instância Brasil)**: R$ 297/mês até 10 leitos · R$ 497 até 25 leitos · Enterprise sob medida. Leito = paciente internado simultâneo; limite **suave** (nunca bloqueia admissão; estouro → aviso ao admin). Usuários ilimitados. WhatsApp ao tutor = add-on opt-in cobrado por mensagem. Marca: **PlantãoVet** (domínios plantao.vet e plantaovet.com.br). Outros países terão a própria tabela, na própria moeda.

> **Linguagem de venda**: a passagem de plantão **reduz drasticamente** a perda de informação entre turnos — não "elimina". O sistema garante que tudo o que foi **registrado** chega ao próximo plantão, e sinaliza turno encerrado sem boletim; o que ninguém anotou, nenhum software adivinha (achado clínico 7).

## 2. Regras de domínio (a espinha dorsal)

> Nomes de entidade, campo e enum abaixo são os identificadores reais do código (inglês, ADR-0004); o termo em português está no glossário.

### Prescription → Scheduling → Task
- **Prescription** é a regra: fármaco/cuidado, dose, via, frequência, duração, criticidade, janela de tolerância. Três tipos (`kind`):
  - `recurring`: gera tarefas pontuais (ex.: dipirona q8h por 3 dias → 9 tarefas).
  - `continuous`: terapia contínua (fluidoterapia, com `details.rate_ml_h`) que gera tarefas de **checagem** periódica (ex.: conferir bomba q2h).
  - `prn` ("se necessário"): sem agenda; permite registrar execuções avulsas, com `max_doses_24h` e `min_interval_minutes` opcionais.
- **`category`** (independente de `kind`) classifica a linha da ficha: `medication | fluids | monitoring | nutrition | care | procedure`. É o que permite agrupar a grade como a prancheta, o jejum bloquear `nutrition`, e o contador de dose agregar por `details.drug` normalizado.
- **Frequência em MINUTOS** (`frequency_minutes`): UTI monitora vitais a cada 15–60 min (pesquisa §1) — hora inteira não serve.
- **Scheduling**: horários derivam da frequência + **horários-âncora da clínica** (`clinics.anchors`, chaveado por minutos; default UFMS: `1440`→10h; `720`→10h/22h; `480`→10h/18h/02h; `360`→10h/16h/22h/04h). Frequências sem âncora: offset a partir de `starts_at`. Exibir sempre horários concretos, nunca SID/BID/TID.
- **`first_dose_now`** (bool): admissão às 14h com dipirona q8h e âncoras 10/18/02 deixaria o paciente 4h sem analgesia. Com a flag, gera tarefa em `starts_at` e **suprime a próxima âncora** se a distância for menor que `frequency_minutes − tolerância`.
- **Suspensão**: `POST /prescriptions/{id}/suspend` cancela tarefas futuras com rastro; executadas ficam intocadas.
- **Ajuste (titulação)**: `POST /prescriptions/{id}/adjust` cria uma **nova versão** vinculada por `replaces_prescription_id`, cancela as tarefas futuras da anterior e reapraza. Titular fluido é rotina; suspend+create sem vínculo tornaria a auditoria ilegível.
- **Task** tem estados persistidos `pending | done | partial | not_done | cancelled`. **"Atrasada" é computado**, nunca armazenado: `pending && agora > scheduled_for + tolerância`. Board e ficha leem a MESMA fila (bug fatal do Vet Radar — pesquisa §6).
- **Janelas de tolerância (ISMP)**: `critical` → 30 min; `normal` → 60 min; `normal` com `frequency_minutes >= 1440` → 120 min. Editável por prescrição.
- **A janela vale nos dois lados**: executar antes de `scheduled_for − tolerância` exige confirmação explícita e grava `early = true` (administração precoce é erro de medicação tanto quanto atraso).
- **Desfechos de execução**: `done` · `partial` (com `values.dose_given` — vomitar meia dose é rotina) · `not_done` com motivo padronizado (`refused | fasting | unavailable | vet_order | other`+detalhe).
- **Registro retroativo** é permitido (modo emergência: fez agora, documenta depois) e exige **`performed_at`** — a hora real do procedimento, não a do apontamento (compliance BR: data e hora do procedimento).
- **Guardrails PRN**: execução avulsa de PRN exige `prescription_id` e valida `min_interval_minutes` / `max_doses_24h` — **aviso com override auditado, nunca bloqueio duro** (fricção gera workaround que falsifica o registro — pesquisa §4).
- **Cerimônias do dia** (contato com tutor, evolução diária) são prescrições-default auto-criadas na admissão a partir de `clinics.default_prescriptions` — reusam aprazamento, tolerância e auditoria sem entidade nova.

### Identidade e auditoria
- **Dois modos em qualquer dispositivo** (ADR-0002): pessoal (conta própria + biometria no app) e **estação** (dispositivo logado na clínica; cada ação exige PIN de 4 dígitos do operador).
- **Segurança do modo estação**: token de estação expira em 12h e carrega `station_key_version` conferida a cada request (rotacionar a chave revoga tudo); `POST /auth/pin` tem rate limit com lockout (5 falhas → 15 min por estação) e grava `pin_failed` na auditoria; PIN é **único por clínica** (validado na definição/troca) — dois PINs iguais atribuiriam ato clínico à pessoa errada.
- **Toda mutação clínica** grava na trilha: quem (`actor_name` + `actor_license`/`actor_license_authority`), quando, o quê, e **snapshots `before`/`after`** — sem o antes, "sem rasuras" não se sustenta.
- Trilha é **append-only** com hash-encadeamento (`prev_hash`/`entry_hash`); correção por adendo, nunca update/delete (ADR-0003).
- **Minimização (LGPD)**: o payload da auditoria carrega ids e dados clínicos, **nunca dados de contato do tutor** — o trigger append-only tornaria esse dado inapagável para sempre.
- Papéis v1 (fixos, permissões como dados): `vet` (prescreve + tudo), `tech` (executa/registra), `admin` (gestão da clínica). RBAC customizável = v2 (só UI; schema já suporta).
- **Registro profissional é dado, não schema**: `license_number` + `license_authority` (o valor "CRMV-SP" é conteúdo).
- **Nenhuma tabela de domínio tem DELETE**: remoção é `is_active`/`status`.

### Passagem de plantão (feature-herói)
- **ShiftNote**: registro avulso por texto ou **áudio transcrito**, associado a paciente, feito durante o turno. O áudio bruto é **apagado após a transcrição confirmada** (só o texto integra o prontuário — LGPD, voz de funcionário).
- **HandoverReport**: por paciente — esqueleto determinístico (tarefas done/partial/not_done/pending/atrasadas, eventos, mudanças de prescrição) + resumo narrativo por IA alimentado pelas notas, **no locale da clínica**. Quem sai revisa e aprova.
- Sem aprovação → o plantão seguinte vê TUDO mesmo assim com selo "não revisado"; omissão auditada. Nunca bloquear, nunca esconder.
- **HandoverAck**: aceite explícito paciente a paciente, com pendências e atrasadas visíveis **no próprio ato**; o sistema mede tempo-até-aceite como termômetro de "carimbo em série".
- **Shift** (semana 3): escala com responsável e registro profissional por turno — detecta "turno trocou sem boletim", define quem é o receptor e serve como evidência de conformidade na fiscalização (pesquisa §5.9).

### Compliance (perfil por país; `br` na v1)
O perfil é selecionado por `clinics.compliance_profile` e implementado em `app/compliance/<profile>.py`. Nenhuma regra específica de país vive fora dele. O perfil `br` (CFMV Res. 1321/2020 + 1653/2025) exige:
- Nome + registro profissional automáticos em cada procedimento.
- Evolução diária obrigatória → alerta "internado sem evolução há 24h".
- Exportação PDF do prontuário (cópia ao tutor em 5 dias úteis), com nome + registro por evolução.
- `consent_status`: `consent_recorded | emergency_no_consent` (+ `consent_reason` obrigatório na emergência).
- Desfecho: `discharged | died | left_ama` (nota obrigatória nos dois últimos).
- `is_controlled` na prescrição: exibe aviso e habilita relatório de movimentação.
- Retenção: 5 anos após o último atendimento.
- Fora da v1: receita de controlados / ICP-Brasil / SNCR (verificar vigência antes do lançamento público — semana 4); termos com assinatura digital (v1.5).

### Tabela de preços e conta da internação
- **`PriceListItem`** (semana 2): a clínica cadastra uma vez cada procedimento, medicamento, insumo e **diária por área** (`is_daily_rate`, ex.: UTI, internação geral, isolamento) com nome, categoria, unidade e `price_minor`. Ao prescrever, o vet escolhe o item e o valor vem preenchido — ninguém digita preço dose a dose.
- **O preço é copiado, nunca referenciado para leitura**: `prescriptions.price_minor` guarda o valor vigente no momento da prescrição (e `price_list_item_id` guarda a origem). Reajustar a tabela **não** altera contas já lançadas — requisito de integridade do registro.
- Execução gera `ChargeItem` automaticamente (execução `partial` → item proporcional); a diária é lançada por dia de internação a partir do item marcado como diária da área do box. Moeda vem de `clinics.currency`.
- Extrato acumula por internação; exportável (PDF/CSV). NÃO é sistema financeiro.
- **Semana 1** não tem a tabela: `prescriptions.price_minor` já existe como o valor congelado, e a semana 2 acrescenta `price_list_items` + a coluna `price_list_item_id` (FK nullable, migração trivial) junto com os `charge_items`.

### Tutor (Owner)
- **Entidade própria** com `whatsapp_opt_in_at` — um tutor com 3 animais não pode ter o dado triplicado, e o opt-in é exigência da Meta e da LGPD. Telefone em **E.164** (`phone_e164`), pré-requisito de WhatsApp internacional.
- Tarefa diária de contato + log estruturado.
- Envio de boletim via **WhatsApp API oficial (Meta Cloud API)**, no locale da clínica — add-on por mensagem com margem. Nunca gateway não-oficial (risco de ban).

## 3. Internacionalização (ADR-0004)

1. **A API nunca devolve texto para exibir.** Erros: HTTP status + `{"error": {"code": "<snake_case>", "params": {...}}}`. Um teste percorre as respostas de erro e falha se alguma trouxer prosa. Códigos da v1: `invalid_credentials`, `token_expired`, `operator_required`, `pin_locked_out`, `pin_duplicate`, `station_key_rotated`, `task_already_processed`, `early_confirmation_required`, `prn_guardrail`, `consent_reason_required`, `outcome_note_required`, `pending_tasks_confirmation_required`, `bed_limit_exceeded` (warning), `not_found`, `forbidden`.
2. **Armazenamento canônico, exibição localizada.** UTC no banco; unidades SI (kg, °C); dinheiro em unidade menor + ISO 4217; telefone E.164; enums como códigos. Data, número, moeda e unidade formatados no cliente com `Intl`, a partir de `clinics.locale`, `clinics.currency`, `clinics.unit_system`.
3. **Locale**: resolução `Accept-Language` → `users.locale` → `clinics.locale` → `pt-BR`. O servidor usa locale **só** para conteúdo que ele mesmo gera: PDF do prontuário, boletim da IA, mensagem de WhatsApp. Catálogos em `app/i18n/{pt-BR,en}.json` com helper `translate(key, locale, **params)`.
4. **Catálogos com paridade garantida**: teste falha se `pt-BR` e `en` não tiverem exatamente as mesmas chaves — é o que impede o `en` de apodrecer.
5. **Cliente**: web com `react-i18next`, app com `i18next` + `expo-localization`; `pt-BR` é o idioma-fonte, `en` presente desde a v1. Nenhuma string literal em componente.
6. **Conteúdo do cliente não é traduzido**: nome de prescrição, notas e evolução ficam como a clínica escreveu.
7. **Unidades**: `clinics.unit_system` (`metric | imperial`) afeta só exibição (kg↔lb, °C↔°F); o banco guarda SI sempre.

## 4. Modelo de dados (núcleo — semana 1)

| Tabela | Campos-chave |
|---|---|
| `clinics` | id uuid pk, name, slug unique, **locale text default 'pt-BR'**, **currency char(3) default 'BRL'**, **unit_system enum(metric/imperial) default 'metric'**, **compliance_profile text default 'br'**, timezone text default 'America/Sao_Paulo', anchors jsonb, default_prescriptions jsonb, plan_tier, bed_limit int, station_key_hash, station_key_version int default 1, created_at |
| `users` | id, name, email unique, password_hash, locale text nullable, is_active |
| `memberships` | id, clinic_id fk, user_id fk, role enum(vet/tech/admin), license_number nullable, license_authority nullable, pin_hash nullable, permissions jsonb, is_active · **UNIQUE (clinic_id, user_id)** |
| `kennels` | id, clinic_id, name, area nullable, is_active bool default true |
| `owners` | id, clinic_id, name, phone_e164, tax_id nullable, whatsapp_opt_in_at nullable, is_active |
| `patients` | id, clinic_id, owner_id fk, name, species, breed nullable, weight_kg numeric nullable, notes, is_active |
| `hospitalizations` | id, clinic_id, patient_id fk, kennel_id fk nullable, vet_membership_id fk, status enum(active/discharged/died/left_ama), admitted_at, ended_at nullable, outcome_note text nullable, consent_status enum(consent_recorded/emergency_no_consent), consent_reason text nullable |
| `prescriptions` | id, clinic_id, hospitalization_id fk, kind enum(recurring/continuous/prn), category enum(medication/fluids/monitoring/nutrition/care/procedure), name, details jsonb (dose, route, concentration, `drug` normalizado, `rate_ml_h` p/ contínua), frequency_minutes int nullable, duration_hours int nullable, criticality enum(normal/critical), tolerance_minutes int, first_dose_now bool default false, is_controlled bool default false, max_doses_24h int nullable, min_interval_minutes int nullable, price_minor int nullable, starts_at, ends_at nullable, replaces_prescription_id fk nullable, suspended_at nullable, suspended_by fk nullable, created_by fk |
| `tasks` | id, clinic_id, hospitalization_id fk, prescription_id fk nullable, title, category, scheduled_for timestamptz, criticality, tolerance_minutes, status enum(pending/done/partial/not_done/cancelled), executed_at nullable, executed_by fk nullable, retroactive bool default false, early bool default false, outcome_reason text nullable, values jsonb nullable, price_minor int nullable |
| `audit_entries` | id bigserial, clinic_id, actor_membership_id nullable, actor_name, actor_license nullable, actor_license_authority nullable, action, entity_type, entity_id, payload jsonb (`before`/`after`/`extra`), prev_hash, entry_hash, created_at — **append-only (trigger bloqueia UPDATE/DELETE)** |

**Índices obrigatórios**: `tasks(clinic_id, status, scheduled_for)` · UNIQUE parcial `tasks(prescription_id, scheduled_for) WHERE prescription_id IS NOT NULL` (idempotência do aprazamento) · `audit_entries(clinic_id, id DESC)` · `audit_entries(clinic_id, entity_type, entity_id)` · `hospitalizations(clinic_id, status)` · UNIQUE `memberships(clinic_id, user_id)` · UNIQUE `(id, clinic_id)` em `hospitalizations` e `prescriptions` + FK composta nos filhos (barreira de tenancy no banco).

Semanas 2–4 acrescentam: `charge_items`, `progress_notes`, `shifts`, `shift_notes`, `handover_reports`, `handover_acks`, `owner_contacts`, `whatsapp_messages`.

## 5. Superfície de API (semana 1)

Prefixo `/api/v1`. Auth: JWT Bearer, `exp` 12h. Token pessoal (`kind=personal`, membership) ou de estação (`kind=station`, clínica, com `station_key_version`); em modo estação, mutações clínicas exigem header `X-Operator-Token` obtido via PIN.

**Envelope de listagem**: `{items, next_cursor}` com `?limit=` (default 50, máx 200). **Envelope de erro**: `{"error": {"code": "...", "params": {...}}}`.

| Endpoint | Descrição |
|---|---|
| `POST /auth/login` | email+senha → JWT pessoal |
| `POST /auth/station` | clinic_slug + station_key → JWT de estação |
| `POST /auth/pin` | (estação) PIN → operator token (5 min); rate limit 5/15min, falha auditada |
| `GET/POST/PATCH /kennels` · `/owners` · `/patients` | CRUD sem DELETE (`is_active`); FK de body validada por tenant |
| `POST /hospitalizations` | admitir: `consent_status` (+`consent_reason` na emergência); limite de leitos suave → cria e retorna `warning: bed_limit_exceeded`; cerimônias default criadas do template da clínica |
| `POST /hospitalizations/{id}/outcome` | `outcome: discharged\|died\|left_ama` (+nota obrigatória nos 2 últimos); `confirm_pending_tasks` só é exigido quando há dose JÁ VENCIDA — a alta cancela as futuras por definição, e confirmação que aparece sempre ninguém lê; cancela tarefas futuras com rastro |
| `GET /hospitalizations/{id}` | ficha: internação + prescrições ativas + tarefas da janela |
| `POST /hospitalizations/{id}/prescriptions` | criar prescrição → aprazamento gera tarefas |
| `POST /prescriptions/{id}/suspend` | suspende + cancela tarefas futuras (`FOR UPDATE`) |
| `POST /prescriptions/{id}/adjust` | nova versão vinculada (`replaces_prescription_id`), cancela futuras, reapraza |
| `GET /tasks?from=&to=` | fila por janela explícita com `display_state`; sem janela, default ±12h **mais toda pendente vencida, por mais velha que seja** — atrasada não expira e o painel a conta sem limite inferior; paginada por cursor (horário+id) |
| `GET /board` | por internação ativa: próxima tarefa, contadores, flag `critical_overdue`; mesma fonte da fila |
| `POST /tasks/{id}/execute` | transição **atômica** (`WHERE status='pending'` → 409 `task_already_processed`); body: `values?`, `retroactive?`+`performed_at`, `partial?`, `confirm_early?` |
| `POST /tasks/{id}/not-done` | `reason` enum (+detalhe se `other`) |
| `POST /tasks/ad-hoc` | execução PRN (exige `prescription_id` de kind=prn; valida intervalo/acumulado → 409 `prn_guardrail`, reenvio com `override=true`) ou evento avulso com título livre |
| `GET /audit` | trilha paginada por cursor (vet/admin; tech → 403) |

## 6. Telas (inventário — 28 mockups publicados, UI pt-BR)

**Rotina do plantão (web)**: Board · TreatmentSheet (grade hora×tarefa, a tela-herói, agrupada por `category`) · Nova prescrição (com preview do aprazamento e supressão da âncora) · Passagem de plantão · Evolução diária · Escala de plantão.

**Gestão e prontuário (web)**: Internados · Admissão · Alta/desfecho · Conta da internação · Prontuário em PDF · Comunicação com o tutor · Tabela de preços · Configurações da clínica (âncoras, tolerâncias, turnos, regionalização) · Equipe e acessos (papéis, PINs, chave de estação) · Boxes e ocupação · Trilha de auditoria · Login (pessoal e estação).

**App do plantonista**: Meu turno · Dar baixa · PIN do operador · Não realizada · Registrar evento · Nota de áudio · Paciente · Receber plantão · Alerta na tela bloqueada · Entrar.

**Responsividade (requisito de produto)**: a web é **responsiva de verdade**, não só desktop — a mesma clínica pode operar num monitor de 27", num notebook e num tablet apoiado no balcão. Pontos de quebra mínimos: ≥1280px (layout de duas colunas, como nos mockups), 768–1279px (tablet: coluna lateral colapsa em gaveta, a grade hora×tarefa vira rolagem horizontal com a coluna de prescrição fixa) e <768px (a web degrada para a lista de tarefas; a operação no celular é o app). O **app roda em tablet e celular**: layouts em `flex`/`grid` com largura fluida, alvos de toque ≥44px, e no tablet as telas de turno e paciente ganham duas colunas em vez de esticar a de celular.

As telas de gestão (semanas 2–4) valem como especificação visual do que a semana 1 já prepara no schema.

## 7. Roadmap — 4 semanas, 4 planos

| Semana | Plano | Entrega testável |
|---|---|---|
| 1 | `docs/superpowers/plans/2026-08-31-semana1-fundacao.md` | API core: tenancy, i18n (códigos + catálogos), 2 modos de auth, owners/patients/hospitalizations, prescription→scheduling→tasks, execução atômica, board, auditoria encadeada. **Checkpoint: fotos das fichas reais durante a semana (não bloqueia migrações).** |
| 2 | semana2 | TreatmentSheet web completa (grade por categoria) + Board + ChargeItems + ProgressNote/alerta 24h · UI pt-BR e en |
| 3 | semana3 | App companion (Expo) + push + `shifts` + Passagem de plantão (notas de áudio → IA no locale → boletim → aceite) |
| 4 | semana4 | Pacote compliance `br` (PDF, adendos), WhatsApp tutor, onboarding/templates, deploy GCP (scheduler single-instance), piloto TestFlight/APK, material de venda |

Planos 2–4 são escritos just-in-time (no início de cada semana), pois absorvem o feedback do piloto e das fichas reais.

## 8. Riscos e pendências externas

1. **Validação de domínio**: Marcus não conhece a rotina clínica — fotos das fichas reais + 30 min com vet na semana 1. A revisão clínica (§9) reduziu esse risco, mas não o zera: **os achados 2, 3, 4 e 6 (category, first_dose_now, titulação de fluido, cerimônias default) precisam de confirmação de um veterinário.**
2. **Apple Developer**: conta aberta imediatamente; piloto via TestFlight/APK.
3. **SNCR/controlados**: fora da v1; verificar vigência antes do lançamento público (semana 4).
4. **LGPD**: campanhas com a base do lab → legítimo interesse B2B com opt-out claro. Áudio de plantonista → aviso de privacidade interno + DPA com fornecedor de IA + descarte do áudio bruto. **Tabela de retenção por classe de dado** (clínico: 5 anos inapagável; contato de tutor: eliminável sob pedido) documentada na semana 4.
5. **Escopo da semana 1**: a revisão de engenharia recomendou cortar modo estação e board. Mantidos por decisão de produto — a ficha da semana 2 já roda em estação, e o board é a superfície de demonstração da venda. Mitigação: worker enxuto (um único job) e nenhum verificador de atraso.
6. **i18n**: `en` entra como catálogo real desde a v1, mas a **revisão de idioma por um falante nativo da área veterinária** fica para antes da primeira venda fora do Brasil. E cada país novo exige um perfil de compliance próprio — não é só tradução.


### Permissões por papel (acrescentado na revisão de 31/ago)

Quem pode o quê vive em `src/back/app/permissions.py`, num mapa único, e é
aplicado pela dependência `require(capability)` — que confere o papel de QUEM
AGE: no celular compartilhado, o dono do PIN, não o aparelho.

Duas classes de regra, que não se misturam:

* **Privativo do profissional habilitado** (`LICENSED_ONLY`): prescrever,
  ajustar e suspender prescrição, assinar evolução, dar alta e declarar óbito.
  A clínica não pode delegar a quem não tem registro no conselho — nem que
  queira. Um teste parametrizado quebra se alguém acrescentar esses atos a
  `tech` ou `admin`.
* **Política da clínica**: o resto. `tech` executa tarefa, registra evento,
  contata tutor, cadastra paciente. `admin` configura, cobra e audita, mas não
  executa ato clínico — ser dono da clínica não dá registro no conselho.

`GET /auth/me` devolve `role` e `capabilities` para a interface esconder o que
a API recusaria: botão que devolve 403 é pior que botão ausente.

## 9. Decisões da revisão adversarial (2026-08-31)

30 achados em 3 lentes; **27 acatados** e incorporados acima. Os 3 não acatados, com o motivo:

- **Cortar modo estação e `GET /board` da semana 1** (engenharia, media) — recusado: ambos têm consumidor imediato na semana 2 e são mais baratos de construir agora, com o schema fresco, do que empilhados na semana da UI.
- **Assinatura eletrônica avançada (MP 2.200-2) no PDF do tutor** (compliance, media) — adiado para v1.5 junto com os termos assinados; a v1 identifica nome + registro profissional por evolução, o que já supera os concorrentes.
- **Relatório de movimentação de controlados** (compliance, baixa) — só a flag `is_controlled` entra na v1; o relatório fica para quando um cliente pedir.

Achados de gravidade **alta** todos acatados, incluindo os que mudaram o schema: `frequency_minutes`, `category`, `first_dose_now`, `replaces_prescription_id`, entidade `owners`, `performed_at`, before/after + hash na auditoria, transição atômica de tarefa, índice único de idempotência, e hardening do modo estação.
