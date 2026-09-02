import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import "../styles/handover.css";

import { CAN } from "../api/capabilities";
import { ApiError, api, asList } from "../api/client";
import type { ProgressNote, ShiftNote } from "../api/types";
import { PinDialog } from "../components/PinDialog";
import { Gate } from "../components/authz";
import { COUNTER_KEYS, COUNTER_STATE, type CounterKey } from "../components/handoverShared";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  ErrorState,
  Section,
  Skeleton,
  stateColors,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { usePatientContext } from "./Patient";

/** Os quatro campos do SOAP, na ordem em que o vet escreve. Tipados contra
 *  `ProgressNote` para que um campo renomeado na API quebre a compilação. */
const FIELDS = ["subjective", "findings", "assessment", "plan"] as const satisfies readonly (keyof ProgressNote)[];

type FieldKey = (typeof FIELDS)[number];

type Draft = Record<FieldKey, string>;

const EMPTY_DRAFT: Draft = { subjective: "", findings: "", assessment: "", plan: "" };

/** A ficha da internação devolve uma JANELA de tarefas: ±12h em torno de agora
 *  (`TaskService.default_window`), mais toda pendente vencida de qualquer idade
 *  (`queue_criteria`). O painel daqui precisa saber disso: ver `counters`. */
const WINDOW_HOURS = 12;

/** Evolução diária.
 *
 *  Compliance BR (spec §2): a evolução carrega nome e registro profissional, é
 *  obrigatória a cada 24h e NÃO se edita: correção é um novo registro ligado
 *  ao anterior por `amends_progress_note_id`. A tela diz isso na cara do
 *  usuário em vez de deixar a pessoa descobrir tentando.
 *
 *  É uma aba do paciente: nome, espécie, peso, box, dias e vet responsável são
 *  do cabeçalho de `Patient.tsx` e não se repetem aqui. */
export function ProgressNotes() {
  const { detail } = usePatientContext();
  const { t } = useTranslation();
  const { moment } = useClinic();
  const describeError = useApiErrorMessage();
  const hospitalizationId = detail.hospitalization.id;

  const [notes, setNotes] = useState<ProgressNote[] | null>(null);
  const [shiftNotes, setShiftNotes] = useState<ShiftNote[] | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [amends, setAmends] = useState<ProgressNote | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [askPin, setAskPin] = useState(false);

  const load = useCallback(async () => {
    const [noteData, shiftNoteData] = await Promise.all([
      api.progressNotes(hospitalizationId),
      api.shiftNotes(hospitalizationId),
    ]);
    setNotes(
      [...asList(noteData)].sort(
        (a, b) => new Date(b.signed_at).getTime() - new Date(a.signed_at).getTime(),
      ),
    );
    setShiftNotes(
      [...asList(shiftNoteData)].sort(
        (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
      ),
    );
  }, [hospitalizationId]);

  useEffect(() => {
    let alive = true;
    async function run() {
      try {
        await load();
        if (alive) setError(null);
      } catch (err) {
        if (alive) setError(describeError(err));
      }
    }
    void run();
    return () => {
      alive = false;
    };
  }, [load, describeError]);

  const lastNote = notes?.[0] ?? null;
  const since = lastNote ? new Date(lastNote.signed_at) : null;

  /** O que aconteceu, e por quanto tempo para trás isso vale.
   *
   *  Os contadores eram calculados "desde a última evolução" em cima de
   *  `detail.tasks`, que só carrega ±12h: com 30h sem evolução, 18h de
   *  execuções ficavam invisíveis e o quadro afirmava um total que não existia,
   *  num painel que a pessoa lê como conferência antes de assinar.
   *
   *  Pendente e atrasada são exceção: a ficha traz toda pendente vencida, de
   *  qualquer data, então esses dois números são completos. Os executados
   *  valem pelo período coberto, e a tela diz qual é. */
  const windowStartMs = useMemo(() => Date.now() - WINDOW_HOURS * 3_600_000, []);
  const coversLastNote = since !== null && since.getTime() >= windowStartMs;
  const executedFromMs = coversLastNote && since ? since.getTime() : windowStartMs;

  const counters = useMemo<Record<CounterKey, number>>(() => {
    const totals = { done: 0, partial: 0, not_done: 0, pending: 0, overdue: 0 };
    for (const task of detail.tasks) {
      if (task.status === "pending") {
        totals.pending += 1;
        // Atraso é derivado, nunca persistido (spec §2): vem do display_state.
        if (task.display_state === "overdue") totals.overdue += 1;
        continue;
      }
      if (new Date(task.scheduled_for).getTime() < executedFromMs) continue;
      if (task.status === "done") totals.done += 1;
      else if (task.status === "partial") totals.partial += 1;
      else if (task.status === "not_done") totals.not_done += 1;
    }
    return totals;
  }, [detail.tasks, executedFromMs]);

  const recentShiftNotes = useMemo(
    () =>
      (shiftNotes ?? []).filter(
        (note) => !since || new Date(note.created_at).getTime() >= since.getTime(),
      ),
    [shiftNotes, since],
  );

  const filled = FIELDS.some((field) => draft[field].trim().length > 0);

  const submit = useCallback(async () => {
    if (!filled) return;
    setBusy(true);
    try {
      const body: Record<string, unknown> = {};
      for (const field of FIELDS) {
        const value = draft[field].trim();
        if (value) body[field] = value;
      }
      if (amends) body.amends_progress_note_id = amends.id;
      await api.createProgressNote(hospitalizationId, body);
      setDraft(EMPTY_DRAFT);
      setAmends(null);
      await load();
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [hospitalizationId, filled, draft, amends, load, describeError]);

  // Sem histórico e com erro não há tela: um banner sozinho deixaria a pessoa
  // olhando um vazio que parece "nunca houve evolução".
  if (error && notes === null) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  const hoursSince = since
    ? Math.max(0, Math.round((Date.now() - since.getTime()) / 3_600_000))
    : null;
  const overdueNote = hoursSince !== null && hoursSince >= 24;
  const complianceHint =
    hoursSince === null
      ? t("progress.lastNoteNever")
      : overdueNote
        ? t("progress.hoursSince", { n: hoursSince })
        : lastNote
          ? t("progress.lastNote", { date: moment(lastNote.signed_at) })
          : null;

  return (
    <>
      <ErrorBanner message={error} />

      <div className="hv-layout">
        <div style={{ display: "grid", gap: 14 }}>
          <Section title={t("progress.title")} hint={complianceHint}>
            <Gate
              can={CAN.progressNoteSign}
              fallback={
                /* Evolução é ato privativo do profissional habilitado: quem não
                   assina lê o histórico, mas não recebe um formulário que vai
                   levar 403 depois de digitado. */
                <Card>
                  <p style={{ margin: 0, color: "var(--ink-3)", fontSize: 13.5 }}>
                    {t("progress.signRestricted")}
                  </p>
                </Card>
              }
            >
              {amends ? (
                <Card style={{ borderColor: "var(--warn-edge)", background: "var(--warn-bg)" }}>
                  <div className="hv-actions">
                    <span style={{ fontSize: 13.5, fontWeight: 600, color: "var(--warn)" }}>
                      {t("progress.amendingOf", { date: moment(amends.signed_at) })}
                    </span>
                    <Button variant="secondary" onClick={() => setAmends(null)}>
                      {t("progress.cancelAmend")}
                    </Button>
                  </div>
                </Card>
              ) : null}

              {FIELDS.map((field) => (
                <Card key={field}>
                  <label className="hv-soap">
                    <span className="hv-soap-label">{t(`progress.${field}`)}</span>
                    <textarea
                      className="hv-textarea"
                      value={draft[field]}
                      placeholder={t(`progress.${field}Placeholder`)}
                      onChange={(event) =>
                        setDraft((current) => ({ ...current, [field]: event.target.value }))
                      }
                    />
                  </label>
                </Card>
              ))}

              <Card>
                <div style={{ display: "grid", gap: 10 }}>
                  <div className="hv-actions">
                    <Button disabled={!filled || busy} onClick={() => void submit()}>
                      {t("progress.submit")}
                    </Button>
                    {!filled ? <span className="hv-muted">{t("progress.requireOne")}</span> : null}
                  </div>
                  <p className="hv-muted">{t("progress.signHint")}</p>
                  <p className="hv-muted" style={{ fontWeight: 600, color: "var(--ink-2)" }}>
                    {t("progress.immutable")}
                  </p>
                </div>
              </Card>
            </Gate>
          </Section>

          <Section title={t("progress.history")}>
            {notes === null ? (
              <Skeleton rows={2} height={120} />
            ) : notes.length === 0 ? (
              <EmptyState title={t("progress.historyEmpty")} hint={t("progress.signHint")} />
            ) : (
              <div className="hv-timeline">
                {notes.map((note) => (
                  <SignedNote
                    key={note.id}
                    note={note}
                    formatted={moment(note.signed_at)}
                    onAmend={() => setAmends(note)}
                  />
                ))}
              </div>
            )}
          </Section>
        </div>

        <aside className="hv-aside">
          <Section title={t("progress.counters.title")} hint={t("progress.sinceHint")}>
            <Card>
              <div style={{ display: "grid", gap: 12 }}>
                <div className="hv-stats">
                  {COUNTER_KEYS.map((key) => {
                    const colors = stateColors(COUNTER_STATE[key]);
                    return (
                      <div key={key} className="hv-stat">
                        <span className="hv-stat-value" style={{ color: colors.fg }}>
                          {counters[key]}
                        </span>
                        <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                          {t(`progress.tasks.${key}`)}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <p className="hv-muted">
                  {coversLastNote
                    ? t("progress.counters.sinceNote")
                    : t("progress.counters.window", { hours: WINDOW_HOURS })}
                </p>
                <p className="hv-muted">{t("progress.tasksHint")}</p>
              </div>
            </Card>
          </Section>

          {/* A nota de plantão se escreve no aparelho de quem está com o
              paciente na mão, no app do celular, inclusive por áudio. Aqui ela
              só se lê: é insumo para escrever a evolução. */}
          <Section title={t("progress.shiftNotes")}>
            <Card>
              <div style={{ display: "grid", gap: 10 }}>
                {shiftNotes === null ? (
                  <Skeleton rows={2} height={40} />
                ) : recentShiftNotes.length === 0 ? (
                  <p className="hv-muted">{t("progress.shiftNotesEmpty")}</p>
                ) : (
                  recentShiftNotes.map((note) => (
                    <div key={note.id} className="hv-entry">
                      <div className="hv-entry-head">
                        <strong style={{ fontSize: 13.5 }}>{note.author_name}</strong>
                        <span className="tabular" style={{ fontSize: 12, color: "var(--ink-3)" }}>
                          {moment(note.created_at)}
                        </span>
                      </div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 12.5,
                          color: "var(--ink-2)",
                          fontStyle: note.source === "audio" ? "italic" : "normal",
                        }}
                      >
                        {note.text}
                      </p>
                      <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                        {note.source === "audio" ? t("progress.audioNote") : t("progress.typedNote")}
                      </span>
                    </div>
                  ))
                )}
              </div>
            </Card>
          </Section>

          <div className="hv-note-block">{t("progress.footer")}</div>
        </aside>
      </div>

      {askPin ? (
        <PinDialog
          context={detail.patient?.name}
          onDone={() => {
            setAskPin(false);
            void submit();
          }}
          onCancel={() => setAskPin(false)}
        />
      ) : null}
    </>
  );
}

function SignedNote({
  note,
  formatted,
  onAmend,
}: {
  note: ProgressNote;
  formatted: string;
  onAmend: () => void;
}) {
  const { t } = useTranslation();
  const license = [note.author_license_authority, note.author_license].filter(Boolean).join(" ");

  return (
    <Card>
      <div style={{ display: "grid", gap: 10 }}>
        <div className="hv-entry-head">
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <strong style={{ fontSize: 14.5 }}>{note.author_name}</strong>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
              {license || t("progress.noLicense")}
            </span>
            {note.amends_progress_note_id ? (
              <span
                className="hv-seal"
                style={{ color: "var(--warn)", background: "var(--warn-bg)" }}
              >
                {t("progress.addendum")}
              </span>
            ) : null}
          </div>
          <span className="tabular" style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
            {formatted}
          </span>
        </div>

        {FIELDS.map((field) => {
          const value = note[field];
          if (!value) return null;
          return (
            <div key={field} className="hv-soap">
              <span className="hv-soap-label">{t(`progress.${field}`)}</span>
              <p className="hv-soap-text">{value}</p>
            </div>
          );
        })}

        <div className="hv-actions">
          {/* O botão abria um editor que só existe para quem assina: o técnico
              clicava, ganhava um aviso âmbar e um "Cancelar", e nunca um campo
              para escrever. Beco sem saída: some para quem não pode. */}
          <Gate can={CAN.progressNoteSign}>
            <Button
              variant="secondary"
              onClick={onAmend}
              style={{ padding: "9px 14px", fontSize: 13.5 }}
            >
              {t("progress.amend")}
            </Button>
          </Gate>
          <span className="hv-muted">{t("progress.immutable")}</span>
        </div>
      </div>
    </Card>
  );
}
