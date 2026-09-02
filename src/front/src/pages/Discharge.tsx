import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { HospitalizationDetail, Statement } from "../api/types";
import { PinDialog } from "../components/PinDialog";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";

import "../styles/patients.css";

type Outcome = "discharged" | "died" | "left_ama";

const OUTCOMES: Outcome[] = ["discharged", "died", "left_ama"];
/** Óbito e retirada a pedido do tutor não se registram sem uma palavra do vet. */
const OUTCOMES_REQUIRING_NOTE: Outcome[] = ["died", "left_ama"];

export function Discharge() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const { money, day: dayLabel } = useClinic();
  const navigate = useNavigate();
  const describeError = useApiErrorMessage();

  const [detail, setDetail] = useState<HospitalizationDetail | null>(null);
  const [statement, setStatement] = useState<Statement | null>(null);
  const [outcome, setOutcome] = useState<Outcome>("discharged");
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [serverPending, setServerPending] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [askPin, setAskPin] = useState(false);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    async function load(hospitalizationId: string) {
      try {
        const data = await api.hospitalization(hospitalizationId);
        if (alive) {
          setDetail(data);
          setError(null);
        }
      } catch (err) {
        if (alive) setError(describeError(err));
      }
      try {
        const charges = await api.statement(hospitalizationId);
        if (alive) setStatement(charges);
      } catch {
        // A conta é contexto: se não abrir, encerrar a internação segue possível.
      }
    }
    void load(id);
    return () => {
      alive = false;
    };
  }, [id, describeError]);





  /** Pendente AQUI é dose que já venceu, não dose de amanhã.
   *
   *  O cliente contava toda pendente e o servidor só as com
   *  `scheduled_for <= now`, então o aviso aparecia em TODA alta, e o
   *  comentário do backend diz exatamente por que isso é ruim: "confirmação que
   *  aparece sempre é confirmação que ninguém lê". Dar alta cancela as futuras;
   *  é o que a alta significa. */
  const pending = useMemo(
    () =>
      (detail?.tasks ?? []).filter(
        (task) => task.status === "pending" && new Date(task.scheduled_for) <= new Date(),
      ).length,
    [detail],
  );

  const pendingCount = serverPending ?? pending;
  const noteRequired = OUTCOMES_REQUIRING_NOTE.includes(outcome);
  const ready =
    (!noteRequired || note.trim() !== "") && (pendingCount === 0 || confirmed);

  const submit = useCallback(async () => {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      await api.hospitalizationOutcome(id, {
        outcome,
        note: note.trim() || undefined,
        confirm_pending_tasks: confirmed,
      });
      // Nunca devolver a pessoa para uma lista onde o paciente já não aparece.
      // O que se faz DEPOIS da alta é fechar a conta e entregar a cópia do
      // prontuário ao tutor (5 dias úteis, perfil `br`), então é para lá que
      // o fluxo continua, na internação que acabou de encerrar.
      navigate(`/internacao/${id}/conta`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
        return;
      }
      if (err instanceof ApiError && err.code === "pending_tasks_confirmation_required") {
        const reported = err.params.pending;
        setServerPending(typeof reported === "number" ? reported : pending);
        setConfirmed(false);
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [id, outcome, note, confirmed, navigate, describeError, pending]);

  if (!detail) {
    return <p style={{ color: "var(--ink-3)" }}>{error ?? t("common.loading")}</p>;
  }

  const patient = detail.patient;
  const admittedAt = new Date(detail.hospitalization.admitted_at);
  const days = Math.max(
    1,
    Math.round((Date.now() - admittedAt.getTime()) / 86_400_000),
  );
  const chargeItems = (statement?.days ?? []).reduce(
    (total, day) => total + day.items.length,
    0,
  );

  return (
    <div className="patients-split">
      <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
        <header>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <h1 style={{ fontSize: 24 }}>{patient?.name ?? "–"}</h1>
            {detail.hospitalization.consent_status === "emergency_no_consent" ? (
              <span
                style={{
                  fontSize: 11,
                  fontWeight: 700,
                  color: "var(--warn)",
                  background: "var(--warn-bg)",
                  borderRadius: 999,
                  padding: "3px 10px",
                }}
              >
                {t("patients.discharge.consentEmergency")}
              </span>
            ) : null}
          </div>
          <p style={{ margin: "2px 0 0", fontSize: 13.5, color: "var(--ink-2)" }}>
            {[
              t("patients.discharge.title"),
              patient?.species,
              patient?.breed,
              detail.kennel_name,
              t("sheet.admittedFor", { days }),
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </header>

        <ErrorBanner message={error} />

        <Card style={{ display: "grid", gap: 14 }}>
          <div className="patients-eyebrow">{t("patients.discharge.outcome")}</div>
          <div className="chip-group">
            {OUTCOMES.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={outcome === option}
                onClick={() => setOutcome(option)}
                className={outcome === option ? "chip chip-stacked chip-on" : "chip chip-stacked"}
              >
                <span style={{ fontWeight: 600 }}>
                  {t(`patients.discharge.outcome.${option}`)}
                </span>
                <span className="chip-hint">
                  {t(`patients.discharge.outcome.${option}Hint`)}
                </span>
              </button>
            ))}
          </div>

          <Field
            label={
              noteRequired ? t("patients.discharge.noteRequired") : t("patients.discharge.note")
            }
          >
            <textarea
              style={{ ...inputStyle, minHeight: 96, resize: "vertical" }}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder={t("patients.discharge.notePlaceholder")}
              required={noteRequired}
            />
          </Field>
        </Card>

        {pendingCount > 0 ? (
          <div className="patients-warn" role="status">
            <strong>{t("patients.discharge.pending", { pending: pendingCount })}</strong>
            <label className="patients-check">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span style={{ fontSize: 13.5 }}>{t("patients.discharge.confirmPending")}</span>
            </label>
            {!confirmed ? (
              <span style={{ fontSize: 12.5 }}>{t("patients.discharge.confirmRequired")}</span>
            ) : null}
          </div>
        ) : (
          <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
            {t("patients.discharge.pendingNone")}
          </p>
        )}

        <Card style={{ padding: "14px 18px" }}>
          <div className="patients-total">
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, color: "var(--ink-3)" }}>
                {t("patients.discharge.total")}
              </span>
              <strong>{money(statement?.total_minor ?? 0)}</strong>
            </div>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
              {t("patients.discharge.totalHint")}
            </span>
          </div>
        </Card>

        <div className="patients-actions">
          <Button
            onClick={() => void submit()}
            disabled={busy || !ready}
            variant={outcome === "discharged" ? "primary" : "danger"}
          >
            {t(`patients.discharge.submit.${outcome}`, { name: patient?.name ?? "" })}
          </Button>
          <Button variant="secondary" onClick={() => navigate(`/internacao/${id}`)}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>

      <aside className="patients-side">
        <Card style={{ display: "grid", gap: 14 }}>
          <div>
            <div className="patients-eyebrow">{t("patients.discharge.side.title")}</div>
            <p style={{ margin: "3px 0 0", fontSize: 13, color: "var(--ink-3)" }}>
              {t("patients.discharge.side.period", {
                from: dayLabel(admittedAt),
                kennel: detail.kennel_name ?? "–",
              })}
            </p>
          </div>

          <dl className="patients-summary">
            <div>
              <dt>{t("patients.discharge.side.days")}</dt>
              <dd className="tabular">
                {t("patients.discharge.side.daysValue", { days })}
              </dd>
            </div>
            <div>
              <dt>{t("patients.discharge.side.vet")}</dt>
              <dd>
                {detail.vet_name ?? "–"}
                {detail.vet_license ? ` · ${detail.vet_license}` : ""}
              </dd>
            </div>
            <div>
              <dt>{t("patients.discharge.side.prescriptions")}</dt>
              <dd>
                {detail.prescriptions.length === 0 ? (
                  t("patients.discharge.side.prescriptionsNone")
                ) : (
                  <span style={{ display: "grid", gap: 3 }}>
                    {detail.prescriptions
                      .filter((prescription) => !prescription.suspended_at)
                      .map((prescription) => (
                        <span key={prescription.id} style={{ fontSize: 13.5, color: "var(--ink-2)" }}>
                          {prescription.name}
                        </span>
                      ))}
                  </span>
                )}
              </dd>
            </div>
            {statement ? (
              <div>
                <dt>{t("patients.discharge.side.charges")}</dt>
                <dd className="tabular">
                  {t("patients.discharge.side.chargesValue", {
                    items: chargeItems,
                    days: statement.days.length,
                  })}
                </dd>
              </div>
            ) : null}
          </dl>

          <div className="patients-note">{t("patients.discharge.side.footer")}</div>
        </Card>
      </aside>

      {askPin ? (
        <PinDialog
          context={patient?.name}
          onDone={() => {
            setAskPin(false);
            void submit();
          }}
          onCancel={() => setAskPin(false)}
        />
      ) : null}
    </div>
  );
}
