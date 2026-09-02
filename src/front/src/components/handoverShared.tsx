import type { MembershipRoster, MembershipRow, Shift } from "../api/types";

/** Alguém da equipe, pelos campos de identidade.
 *
 *  `GET /memberships` é do administrador (devolve 403 para vet e técnico) e
 *  `GET /memberships/roster` é de qualquer membro. As duas respostas trazem os
 *  mesmos campos de identidade (nome e registro), então as telas de plantão
 *  podem ler o roster sem trocar nenhum helper. */
export type Person = MembershipRow | MembershipRoster;

export function memberName(member: Person): string {
  return member.name || "";
}

/** "CRMV-SP 12345": a autoridade é conteúdo, não schema (spec §2). */
export function memberLicense(member: Person): string | null {
  if (!member.license_number) return null;
  return [member.license_authority, member.license_number].filter(Boolean).join(" ");
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "··";
  const first = parts[0][0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** O turno que está sendo passado AGORA: o último que já começou.
 *
 *  A passagem abria em "todos os turnos", e `GET /handover/reports` ordena por
 *  criação CRESCENTE com teto de 50 e sem cursor: passados uns 50 boletins, a
 *  tela mostrava os mais ANTIGOS que a clínica já produziu, nunca a passagem
 *  de agora. Quando a escala só tem turno futuro (clínica montando a semana),
 *  vale o próximo a começar: é o que a pessoa está prestes a assumir. */
export function shiftBeingHandedOver(shifts: Shift[], now: number = Date.now()): string {
  let started: Shift | null = null;
  let upcoming: Shift | null = null;
  for (const shift of shifts) {
    const begin = new Date(shift.starts_at).getTime();
    if (Number.isNaN(begin)) continue;
    if (begin <= now) {
      if (!started || begin > new Date(started.starts_at).getTime()) started = shift;
    } else if (!upcoming || begin < new Date(upcoming.starts_at).getTime()) {
      upcoming = shift;
    }
  }
  return (started ?? upcoming)?.id ?? "";
}

/** Lista de tarefas do esqueleto do boletim. `overdue` é SUBCONJUNTO de
 *  `pending` (spec §2: atraso é derivado, nunca persistido): somar os dois
 *  contaria a mesma tarefa duas vezes, por isso a UI diz isso em voz alta. */
export const COUNTER_KEYS = ["done", "partial", "not_done", "pending", "overdue"] as const;

export type CounterKey = (typeof COUNTER_KEYS)[number];

/** Cor do contador reusa o vocabulário de estado da UI: pendente é "due". */
export const COUNTER_STATE: Record<CounterKey, string> = {
  done: "done",
  partial: "partial",
  not_done: "not_done",
  pending: "due",
  overdue: "overdue",
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function skeletonCounters(skeleton: Record<string, unknown>): Record<CounterKey, number> {
  const source = asRecord(skeleton.tasks);
  const counters = {} as Record<CounterKey, number>;
  for (const key of COUNTER_KEYS) {
    const value = source[key];
    counters[key] = typeof value === "number" ? value : 0;
  }
  return counters;
}

export function skeletonPeriod(skeleton: Record<string, unknown>): {
  since: string | null;
  until: string | null;
} {
  const period = asRecord(skeleton.period);
  return {
    since: typeof period.since === "string" ? period.since : null,
    until: typeof period.until === "string" ? period.until : null,
  };
}

export function skeletonNoteCount(skeleton: Record<string, unknown>): number {
  return Array.isArray(skeleton.notes) ? skeleton.notes.length : 0;
}
