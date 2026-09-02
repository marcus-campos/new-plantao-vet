# Internação Veterinária (Veterinary Inpatient Care)

Sistema web (desktop-first) + app companion que coordena a rotina de internação de clínicas veterinárias: medicações e cuidados agendados, visão geral da internação e passagem de plantão rastreável.

**Glossário bilíngue.** O identificador em inglês é o nome canônico no código, banco e API (ADR-0004); o termo em português é como a equipe brasileira fala e como a UI pt-BR rotula. Ao ler ou escrever código, use a coluna em inglês; ao falar com clínica, cliente ou nas telas, a portuguesa.

## Language

### Núcleo

**Clinic** · pt-BR: *Clínica*
A organização cliente (tenant). Toda entidade do sistema pertence a uma clínica, e é ela que define locale, moeda, fuso, unidades, horários-âncora e perfil de compliance.
_Avoid_: hospital, empresa, conta, Tenant

**Hospitalization** · pt-BR: *Internação*
O episódio de um paciente entre admissão e desfecho. Prescrições, tarefas e eventos pertencem à internação, não ao paciente.
_Avoid_: Admission (é o evento, não o episódio), Stay, Visit, estadia

**Patient** · pt-BR: *Paciente*
O animal internado.
_Avoid_: Animal, Pet

**Owner** · pt-BR: *Tutor*
A pessoa responsável pelo paciente. Entidade própria (um tutor pode ter vários pacientes) e titular de dados pessoais, com opt-in de WhatsApp próprio.
_Avoid_: Client, Guardian, Tutor (falso cognato em inglês), dono

**Kennel** · pt-BR: *Box*
A localização física do paciente na clínica (box, baia, gaiola, UTI). Não confundir com Bed, que é capacidade de cobrança.
_Avoid_: Cage, Box, Location, leito (para localização)

**Bed** · pt-BR: *Leito*
A unidade de capacidade e cobrança: um paciente internado simultaneamente. O limite do plano é suave — nunca bloqueia uma admissão; o estouro gera aviso ao admin.
_Avoid_: Slot, Seat, vaga

### Trabalho clínico

**Prescription** · pt-BR: *Prescrição*
A regra clínica criada pelo veterinário que gera tarefas: fármaco ou cuidado, dose, via, frequência (em minutos), duração, criticidade e janela de tolerância. Três `kind`s: `recurring`, `continuous` (com taxa, gera checagens) e `prn` ("se necessário", sem agenda). Ajuste de dose ou taxa cria uma **nova versão vinculada** (`replaces_prescription_id`), nunca edita a anterior.
_Avoid_: Order, Treatment, Medication, receita

**Task** · pt-BR: *Tarefa*
A menor unidade de execução — uma dose, checagem ou cuidado de um paciente previsto para um horário. Nasce do aprazamento de uma prescrição; no caso de PRN, é registrada avulsa.
_Avoid_: Activity, Item, Alert, atividade

**Scheduling** · pt-BR: *Aprazamento*
A derivação dos horários concretos das tarefas a partir da frequência da prescrição e dos horários-âncora da clínica (ex.: q8h → 10h/18h/02h). Vive em `SchedulingService`, função pura e sem I/O.
_Avoid_: Planning, agendamento

**ProgressNote** · pt-BR: *Evolução*
O registro clínico diário de cada internado, assinado pelo veterinário. Obrigatório no perfil de compliance brasileiro (CFMV Res. 1321/2020).
_Avoid_: Note, Evolution (falso cognato), anotação

**PriceListItem** · pt-BR: *Item da tabela de preços*
Um procedimento, medicamento, insumo ou diária que a clínica cadastra uma vez com seu preço, e que passa a preencher o valor automaticamente ao prescrever. Alterar o preço aqui **nunca** muda contas já lançadas — o valor é copiado no momento da prescrição.
_Avoid_: Product, Service, Catalog, procedimento

**ChargeItem** · pt-BR: *Item da conta*
Um item cobrável gerado automaticamente pela execução de uma tarefa, com o valor congelado no momento em que a prescrição foi feita. O conjunto forma a conta da internação — um extrato exportável, não um sistema financeiro.
_Avoid_: Invoice, Billing, fatura

### Turno e passagem

**Shift** · pt-BR: *Plantão*
O turno de trabalho na internação, com escala e profissional responsável (com registro profissional). É o que permite ao sistema saber que houve troca — e detectar turno encerrado sem boletim.
_Avoid_: Duty, Rotation, turno

**ShiftNote** · pt-BR: *Nota de plantão*
Um registro avulso feito durante o turno, por texto ou áudio transcrito, associado a um paciente. Alimenta o rascunho do boletim.
_Avoid_: Comment, Observation, observação

**HandoverReport** · pt-BR: *Boletim de plantão*
O resumo do turno por paciente: esqueleto determinístico (tarefas feitas, parciais, não feitas, pendentes, atrasadas; eventos; mudanças de prescrição) + resumo narrativo da IA a partir das notas, revisado e aprovado por quem entrega o plantão.
_Avoid_: Summary, Report, relatório de turno

**HandoverAck** · pt-BR: *Aceite de plantão*
O aceite explícito de quem recebe o plantão, paciente a paciente, com as pendências visíveis no próprio ato. É o elemento de maior impacto do protocolo I-PASS.
_Avoid_: Confirmation, Signoff

### Interfaces e identidade

**Board** · pt-BR: *Painel*
Tela somente-leitura com a visão geral da internação (pacientes, próximas tarefas, atrasos). Uma clínica pode ter zero, um ou vários painéis abertos — é só uma URL em tela cheia, não um equipamento obrigatório. Lê a mesma fila de tarefas da ficha, nunca uma fonte paralela.
_Avoid_: Dashboard, Whiteboard, TV

**TreatmentSheet** · pt-BR: *Ficha da internação*
A tela-herói: a grade hora × tarefa de um paciente, agrupada por categoria — o equivalente digital da prancheta de papel presa ao box.
_Avoid_: Chart, Flowsheet, prontuário (que é o registro completo, não esta tela)

**StaffApp** · pt-BR: *App do plantonista*
O aplicativo móvel companion: tarefas do turno, alertas, baixa de tarefas, notas de áudio e consulta rápida do paciente. A ficha completa e a gestão vivem no navegador.
_Avoid_: Pager, Mobile app completo

**StationMode** · pt-BR: *Modo estação*
Um dispositivo (computador ou celular) logado na clínica e compartilhado pela equipe, em que cada ação exige o PIN do operador. Contrasta com o modo pessoal, em que o profissional usa a própria conta com biometria.
_Avoid_: Kiosk, modo compartilhado

**Membership** · pt-BR: *Vínculo*
A participação de um usuário numa clínica, com papel (`vet`, `tech`, `admin`), registro profissional (`license_number` + `license_authority`) e PIN de operador. É o membership — não o usuário — que assina atos clínicos.
_Avoid_: Role, Staff, Employee

**AuditEntry** · pt-BR: *Entrada de auditoria*
Um registro imutável de mutação clínica, com autor (nome e registro profissional), instante, `before`/`after` e hash encadeado. A tabela é append-only imposta pelo banco (ADR-0003).
_Avoid_: Log, History
