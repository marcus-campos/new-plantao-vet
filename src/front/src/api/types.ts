/** Tipos do contrato da API (src/back). Identificadores em inglês (ADR-0004). */

export type DisplayState = "on_time" | "due" | "overdue" | "done" | "partial" | "not_done" | "cancelled";
export type Criticality = "normal" | "critical";
export type PrescriptionKind = "recurring" | "continuous" | "prn";
export type PrescriptionCategory =
  | "medication"
  | "fluids"
  | "monitoring"
  | "nutrition"
  | "care"
  | "procedure";
export type OutcomeReason = "refused" | "fasting" | "unavailable" | "vet_order" | "other";

export interface ApiErrorBody {
  error: { code: string; params: Record<string, unknown> };
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface Me {
  kind: "personal" | "station";
  clinic_id: string;
  membership_id: string | null;
  role: "vet" | "tech" | "admin" | null;
  /** O que este usuário pode fazer. A interface esconde o resto. */
  capabilities: string[];
  /** Se já existe um PIN. Decide se a troca pede o atual: exigir um valor que
   *  não existe deixaria de fora quem nunca definiu um. */
  has_pin: boolean;
}

/** Quem digitou o PIN, e o que pode.
 *
 *  `/auth/me` devolve papel nulo em modo estação de propósito (o aparelho não
 *  tem papel próprio), e o cliente concluía "posso tudo": a IA de gestão
 *  inteira aparecia num tablet de corredor. Esta é a pergunta certa. */
export interface Operator {
  membership_id: string;
  name: string;
  role: "vet" | "tech" | "admin";
  license_number: string | null;
  license_authority: string | null;
  capabilities: string[];
}

export interface Task {
  id: string;
  hospitalization_id: string;
  prescription_id: string | null;
  title: string;
  category: PrescriptionCategory;
  scheduled_for: string;
  criticality: Criticality;
  tolerance_minutes: number;
  status: string;
  display_state: DisplayState;
  executed_at: string | null;
  executed_by: string | null;
  retroactive: boolean;
  early: boolean;
  outcome_reason: string | null;
  values: Record<string, unknown> | null;
  price_minor: number | null;
}

export interface Prescription {
  id: string;
  hospitalization_id: string;
  kind: PrescriptionKind;
  category: PrescriptionCategory;
  name: string;
  details: Record<string, unknown>;
  frequency_minutes: number | null;
  criticality: Criticality;
  tolerance_minutes: number;
  first_dose_now: boolean;
  is_controlled: boolean;
  max_doses_24h: number | null;
  min_interval_minutes: number | null;
  price_minor: number | null;
  starts_at: string;
  ends_at: string | null;
  replaces_prescription_id: string | null;
  suspended_at: string | null;
}

/** A dose deste fármaco para ESTE paciente: o resultado E o caminho.
 *
 *  O que o veterinário confere não é o número: é a conta. "0,27 ml" sozinho não
 *  se verifica; "0,15 mg/kg × 3,6 kg ÷ 2 mg/ml" se verifica num relance. */
export interface DosePreview {
  dose_per_kg: string | null;
  weight_kg: string | null;
  dose_mg: string | null;
  concentration_mg_per_ml: string | null;
  volume_ml: string | null;
  /** Códigos, nunca prosa (ADR-0004): no_rule, unreviewed_rule, contraindicated,
   *  breed_sensitivity, above_range, below_range, capped, fixed_dose,
   *  no_weight, no_concentration. */
  warnings: string[];
  /** Texto escrito pela clínica. Conteúdo do cliente não é traduzido. */
  notes: string[];
  rule_id: string | null;
  reviewed: boolean;
  reviewed_by_name: string | null;
  dose_min_per_kg: string | null;
  dose_max_per_kg: string | null;
  frequency_minutes: number | null;
  species: string | null;
  unit_label: string | null;
}

/** O aprazamento calculado pelo servidor. Uma regra, um lugar. */
export interface SchedulePreview {
  times: string[];
  /** Quantas âncoras a "primeira dose agora" suprimiu: a explicação de por que
   *  a segunda dose não caiu no horário-padrão. */
  suppressed: number;
  tolerance_minutes: number;
  /** As âncoras da clínica para esta frequência. Vazio = offset do início. */
  anchors: string[];
}

export interface BoardCounters {
  /** Pendentes por estado: o que RESTA. */
  on_time: number;
  due: number;
  overdue: number;
  /** Executadas e previstas no dia da clínica. "N de M feitas" contava
   *  pendentes: dizia "9 de 12 feitas" com zero feitas. */
  done_today: number;
  planned_today: number;
}

/** Por que este paciente precisa de atenção. Ausente = está em dia.
 *
 *  `magnitude` é minutos de atraso, ou HORAS sem evolução quando
 *  `reason === "no_progress_note"`. O código vira frase no cliente: a API
 *  nunca devolve prosa (ADR-0004). */
export type AttentionReason = "critical_overdue" | "overdue" | "no_progress_note" | "due";

export interface BoardAttention {
  reason: AttentionReason;
  severity: number;
  magnitude: number | null;
  task_title: string | null;
}

export interface BoardRow {
  hospitalization_id: string;
  patient_id: string;
  patient_name: string;
  species: string | null;
  /** Vem junto do nome porque o mapa de boxes cruzava paciente e box por
   *  string: renomear um box ocupado mudava o paciente de lugar. */
  kennel_id: string | null;
  kennel_name: string | null;
  admitted_at: string;
  next_task: Task | null;
  counters: BoardCounters;
  critical_overdue: boolean;
  attention: BoardAttention | null;
}

export interface BoardShift {
  id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  membership_id: string;
  member_name: string | null;
  is_vet_responsible: boolean;
  /** O plantão é de quem está olhando. */
  is_mine: boolean;
}

export interface Board {
  /** Relógio do servidor e fuso da CLÍNICA. Todo `scheduled_for` é calculado no
   *  fuso da clínica; formatá-lo com o relógio do aparelho fazia um quiosque em
   *  UTC mostrar a dose das 10h como 13h. */
  now: string;
  timezone: string;
  shifts: BoardShift[];
  totals: {
    patients: number;
    due: number;
    overdue: number;
    /** Quantos pacientes têm motivo de atenção. É a manchete. */
    attention: number;
    /** A fila em baldes de tempo. */
    now: number;
    next_hour: number;
    later: number;
  };
  rows: BoardRow[];
}

export interface PatientSummary {
  id: string;
  owner_id: string;
  name: string;
  species: string;
  breed: string | null;
  weight_kg: string | null;
}

export interface Hospitalization {
  id: string;
  patient_id: string;
  kennel_id: string | null;
  vet_membership_id: string;
  status: "active" | "discharged" | "died" | "left_ama";
  admitted_at: string;
  ended_at: string | null;
  outcome_note: string | null;
  consent_status: string;
  consent_reason: string | null;
}

export interface HospitalizationDetail {
  hospitalization: Hospitalization;
  patient: PatientSummary | null;
  kennel_name: string | null;
  vet_name: string | null;
  vet_license: string | null;
  prescriptions: Prescription[];
  tasks: Task[];
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Patient {
  id: string;
  owner_id: string;
  name: string;
  species: string;
  breed: string | null;
  weight_kg: string | null;
  notes: string | null;
  is_active: boolean;
}

export interface PatientIdentifier {
  id: string;
  kind: string;
  value: string;
}

/** Resultado da busca única: nome do paciente, identificador, nome ou documento
 *  do responsável. Tudo numa caixa só. */
export interface PatientSearchHit {
  id: string;
  name: string;
  species: string;
  breed: string | null;
  owner_id: string;
  owner_name: string;
  identifiers: PatientIdentifier[];
  /** Preenchido quando o paciente JÁ está internado: abre a ficha, não interna de novo. */
  active_hospitalization_id: string | null;
}

/** Como esta clínica identifica um paciente. Vem do perfil de compliance, não
 *  do código: por isso a mesma tela pede microchip na veterinária e CPF/CNS na
 *  saúde humana. */
export interface IdentifierKind {
  kind: string;
  label_key: string;
  pattern: string | null;
}

export interface ClinicProfile {
  profile: string;
  /** Regionalização, aberta a todo membro: toda tela precisa formatar hora e
   *  dinheiro, e `ClinicSettings` (que carrega plano e limite de leitos) é do
   *  administrador. */
  locale: string;
  currency: string;
  unit_system: "metric" | "imperial";
  timezone: string;
  /** Chave de tradução do nome da área: "Veterinária", "Saúde humana". */
  name_key: string;
  responsible_label_key: string;
  patient_identifier_kinds: IdentifierKind[];
  retention_years: number;
  license_authority_label_key: string;
  /** Estado da assinatura, para a interface avisar (teste acabando, boleto em
   *  atraso) sem expor nada de comercial. */
  subscription_status: "trial" | "active" | "past_due" | "suspended" | "cancelled";
  trial_ends_at: string | null;
}

export interface Kennel {
  id: string;
  name: string;
  area: string | null;
  is_active: boolean;
}

export interface Owner {
  id: string;
  name: string;
  phone_e164: string;
  tax_id: string | null;
  whatsapp_opt_in_at: string | null;
  is_active: boolean;
}

/* ---- Semanas 2-4 ---------------------------------------------------- */

export type ChargeSource = "task_execution" | "daily_rate" | "manual";
export type ContactChannel = "phone" | "whatsapp" | "in_person";
export type ContactDirection = "outbound" | "inbound";
export type ShiftNoteSource = "typed" | "audio";

export interface PriceListItem {
  id: string;
  code: string | null;
  name: string;
  category: PrescriptionCategory;
  unit: string | null;
  price_minor: number;
  is_daily_rate: boolean;
  kennel_area: string | null;
  is_controlled: boolean;
  is_active: boolean;
  /** mg/ml da apresentação: o número que transforma a dose em volume na
   *  seringa. Sem ele o cálculo para em mg e a interface diz isso. */
  concentration_mg_per_ml: string | null;
  /** Quantas posologias CONFERIDAS este item tem. Zero num fármaco significa
   *  que a calculadora não vai pré-preencher nada quando alguém o prescrever,
   *  e é isso que a lista precisa deixar à vista. */
  reviewed_dose_rules: number;
}

/** A posologia de uma apresentação para uma espécie.
 *
 *  Mora no item de preço porque é lá que o fármaco já é definido uma vez: uma
 *  tela separada de posologias seria um segundo cadastro do mesmo remédio. */
export interface DoseRule {
  id: string;
  price_list_item_id: string;
  /** null vale para qualquer espécie. A regra da espécie vence a genérica. */
  species: string | null;
  route: string | null;
  dose_min_per_kg: string | null;
  dose_max_per_kg: string | null;
  dose_default_per_kg: string | null;
  /** Dose por ANIMAL. Multiplicá-la pelo peso É o erro que ela evita. */
  fixed_dose_mg: string | null;
  max_total_mg: string | null;
  frequency_minutes: number | null;
  is_contraindicated: boolean;
  warning: string | null;
  breed_warning: string | null;
  /** Raças que disparam o aviso, separadas por vírgula. */
  breeds: string | null;
  source: string | null;
  notes: string | null;
  /** Null significa que ninguém conferiu: a regra não pré-preenche nada. */
  reviewed_at: string | null;
  reviewed_by_name: string | null;
  is_active: boolean;
}

export interface ChargeItem {
  id: string;
  hospitalization_id: string;
  task_id: string | null;
  price_list_item_id: string | null;
  description: string;
  quantity: string;
  unit_price_minor: number;
  total_minor: number;
  charged_at: string;
  source: ChargeSource;
}

export interface ChargeDay {
  date: string;
  items: ChargeItem[];
  total_minor: number;
}

export interface Statement {
  total_minor: number;
  currency: string;
  days: ChargeDay[];
}

export interface ProgressNote {
  id: string;
  hospitalization_id: string;
  author_name: string;
  author_license: string | null;
  author_license_authority: string | null;
  subjective: string | null;
  findings: string | null;
  assessment: string | null;
  plan: string | null;
  amends_progress_note_id: string | null;
  signed_at: string;
}

export interface MissingProgressNoteAlert {
  hospitalization_id: string;
  /** Nulável: o backend devolve null quando o paciente foi apagado do vínculo. */
  patient_name: string | null;
  /** Horas desde a ÚLTIMA evolução; null quando nunca houve nenhuma. */
  hours_since: number | null;
}

export interface ComplianceAlerts {
  missing_progress_note: MissingProgressNoteAlert[];
}

export interface Shift {
  id: string;
  name: string;
  starts_at: string;
  ends_at: string;
  membership_id: string;
  is_vet_responsible: boolean;
  closed_at: string | null;
}

export interface ShiftNote {
  id: string;
  hospitalization_id: string;
  shift_id: string | null;
  author_name: string;
  text: string;
  source: ShiftNoteSource;
  created_at: string;
}

export interface HandoverReport {
  id: string;
  hospitalization_id: string;
  from_shift_id: string | null;
  to_shift_id: string | null;
  skeleton: Record<string, unknown>;
  narrative: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  created_at: string;
  patient_name: string | null;
  kennel_name: string | null;
  acked_at: string | null;
  acked_by_name: string | null;
  seconds_to_ack: number | null;
  /** O que continua em aberto AGORA. "3 pendentes" é um número; "Glicemia das
   *  16h, atrasada 3h" é a coisa a fazer. A spec exige as pendências visíveis
   *  no próprio ato do aceite, o elemento mais negligenciado do I-PASS. */
  open_tasks: Task[];
}

export interface ClinicSettings {
  /** Área de atuação: decide identificadores do paciente e retenção. */
  compliance_profile: string;
  name: string;
  /** Timbre do prontuário entregue ao tutor. A tela lia esses três campos por
   *  cast e eles nunca existiram: o documento saía sem endereço nem CNPJ. */
  address: string | null;
  phone: string | null;
  tax_id: string | null;
  slug: string;
  locale: string;
  currency: string;
  unit_system: "metric" | "imperial";
  timezone: string;
  anchors: Record<string, string[]>;
  default_prescriptions: Record<string, unknown>[];
  /** Janelas de tolerância da clínica, em minutos. É delas que sai o que o
   *  sistema inteiro chama de "atrasada": o estado é derivado na leitura. */
  tolerance_critical_minutes: number;
  tolerance_normal_minutes: number;
  tolerance_daily_minutes: number;
  plan_tier: string | null;
  /** O nome do plano: o código é chave, o nome é o que se lê. */
  plan_name: string | null;
  bed_limit: number | null;
  station_key_version: number;
  active_hospitalizations: number;
}

/** Um aparelho compartilhado da clínica: o tablet do corredor, o balcão.
 *
 *  Substitui a chave de estação, que era UMA senha para a clínica inteira:
 *  revogar era tudo ou nada, ninguém sabia quais aparelhos existiam, e o
 *  bloqueio por erro de PIN sumia quando a pessoa relogava. */
export interface StationDevice {
  id: string;
  name: string;
  status: "pending" | "active" | "revoked";
  /** Responde "este aparelho ainda está em uso?", que é a única pergunta que
   *  faz alguém decidir revogar um. */
  last_seen_at: string | null;
  created_at: string;
  approved_at: string | null;
  approved_by_name: string | null;
  revoked_at: string | null;
  /** Preenchido = travado por cinco PINs errados. Sair daí é ato de um
   *  administrador: o bloqueio não expira sozinho. */
  pin_locked_at: string | null;
  pin_failed_attempts: number;
  /** Enquanto está no futuro, o código de liberação ainda vale. */
  enrollment_expires_at: string | null;
}

export interface MembershipRow {
  id: string;
  user_id: string;
  name: string;
  email: string;
  role: "vet" | "tech" | "admin";
  license_number: string | null;
  license_authority: string | null;
  has_pin: boolean;
  is_active: boolean;
}

export interface OwnerContact {
  id: string;
  hospitalization_id: string;
  owner_id: string;
  channel: ContactChannel;
  direction: ContactDirection;
  summary: string;
  sent_at: string;
  delivered_at: string | null;
  read_at: string | null;
  author_name: string;
}

export interface AuditEntry {
  id: number;
  actor_name: string;
  actor_license: string | null;
  actor_license_authority: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  payload: { before?: Record<string, unknown> | null; after?: Record<string, unknown> | null; extra?: Record<string, unknown> | null };
  entry_hash: string;
  created_at: string;
}

export interface MedicalRecord {
  generated_at: string;
  clinic_name: string;
  patient: PatientSummary | null;
  owner_name: string | null;
  hospitalization: Hospitalization;
  vet: { name: string; license_number: string | null; license_authority: string | null } | null;
  /** Seção ausente = não pedida em `include`; pedida e vazia = []. */
  progress_notes?: ProgressNote[] | null;
  tasks?: Record<string, unknown>[] | null;
  prescriptions?: Prescription[] | null;
  charges?: Record<string, unknown>[] | null;
}

/** Roster enxuto: legível por qualquer membro (a escala mostra quem está de plantão). */
export interface MembershipRoster {
  id: string;
  name: string;
  role: "vet" | "tech" | "admin";
  license_number: string | null;
  license_authority: string | null;
  is_active: boolean;
}

/* ---- Plataforma: quem vende e dá suporte ---------------------------------- */

export interface PlatformMe {
  id: string;
  name: string;
  email: string;
}

export type SubscriptionStatus = "trial" | "active" | "past_due" | "suspended" | "cancelled";

/** Uma clínica na lista do back-office. Os três contadores respondem as três
 *  perguntas de quem vende: tem gente? tem paciente? ainda usa? */
export interface PlatformClinicRow {
  id: string;
  name: string;
  slug: string;
  plan_tier: string | null;
  bed_limit: number | null;
  subscription_status: SubscriptionStatus;
  trial_ends_at: string | null;
  contact_name: string | null;
  created_at: string;
  members: number;
  active_hospitalizations: number;
  last_activity_at: string | null;
}

export interface PlatformMember {
  membership_id: string;
  user_id: string;
  name: string;
  email: string;
  role: "vet" | "tech" | "admin";
  license_number: string | null;
  license_authority: string | null;
  has_pin: boolean;
  is_active: boolean;
}

export interface PlatformClinic extends PlatformClinicRow {
  locale: string;
  currency: string;
  timezone: string;
  compliance_profile: string;
  contact_email: string | null;
  contact_phone: string | null;
  support_notes: string | null;
  suspended_at: string | null;
  station_key_version: number;
  members_list: PlatformMember[];
  devices: { id: string; name: string; status: string; last_seen_at: string | null; pin_locked_at: string | null }[];
  recent_audit: { id: number; actor_name: string; action: string; entity_type: string; created_at: string }[];
}

/** Um plano comercial. Nasce, vive enquanto ativo, aposenta (ninguém novo
 *  entra) e migra (todo mundo vai para outro). "Fundador" é só um plano que
 *  um dia se aposenta com um clique. */
export interface Plan {
  code: string;
  name: string;
  bed_limit: number | null;
  price_minor: number;
  currency: string;
  /** Maior que zero faz dele um plano de TESTE. */
  trial_days: number;
  is_active: boolean;
  sort_order: number;
  notes: string | null;
  created_at: string;
  retired_at: string | null;
  /** Quantas clínicas estão nele agora. */
  clinics: number;
}
