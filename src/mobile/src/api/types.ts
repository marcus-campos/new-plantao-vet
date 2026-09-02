/** Tipos do contrato da API (src/back). Identificadores em inglês, conforme o ADR-0004. */

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

export interface BoardCounters {
  on_time: number;
  due: number;
  overdue: number;
}

export interface BoardRow {
  hospitalization_id: string;
  patient_name: string;
  kennel_name: string | null;
  next_task: Task | null;
  counters: BoardCounters;
  critical_overdue: boolean;
}

export interface Board {
  totals: { patients: number; due: number; overdue: number; on_time_rate: number };
  rows: BoardRow[];
}

export interface PatientSummary {
  id: string;
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
  day: string;
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

export interface ComplianceAlerts {
  missing_progress_note: {
    hospitalization_id: string;
    patient_name: string;
    hours_since: number | null;
  }[];
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
}

export interface ClinicSettings {
  name: string;
  slug: string;
  locale: string;
  currency: string;
  unit_system: "metric" | "imperial";
  timezone: string;
  anchors: Record<string, string[]>;
  default_prescriptions: Record<string, unknown>[];
  plan_tier: string | null;
  bed_limit: number | null;
  station_key_version: number;
  active_hospitalizations: number;
}

export interface MembershipRow {
  id: string;
  user_name: string;
  user_email: string;
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
  clinic: Record<string, unknown>;
  patient: Record<string, unknown>;
  owner: Record<string, unknown>;
  hospitalization: Record<string, unknown>;
  vet: Record<string, unknown>;
  progress_notes?: ProgressNote[];
  executions?: Record<string, unknown>[];
  prescriptions?: Prescription[];
  generated_at: string;
}
