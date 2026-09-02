import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { OutcomeReason, Task } from "../api/types";
import { Button, ErrorBanner, inputStyle, useApiErrorMessage } from "./ui";

const REASONS: OutcomeReason[] = ["refused", "fasting", "unavailable", "vet_order", "other"];

export function NotDoneDialog({
  task,
  onClose,
  onDone,
  onError,
}: {
  task: Task;
  onClose: () => void;
  onDone: () => void | Promise<void>;
  onError: (message: string) => void;
}) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [reason, setReason] = useState<OutcomeReason>("fasting");
  const [detail, setDetail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      await api.notDoneTask(
        task.id,
        reason,
        reason === "other" ? { outcome_detail: detail } : undefined,
      );
      await onDone();
    } catch (err) {
      const message = describeError(err);
      setError(message);
      onError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 19 }}>{t("task.notDone")}</h2>
        <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{task.title}</p>

        <ErrorBanner message={error} />

        <div style={{ display: "grid", gap: 8 }}>
          {REASONS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setReason(option)}
              style={{
                textAlign: "left",
                border: `${reason === option ? 2 : 1}px solid ${reason === option ? "var(--primary)" : "var(--line)"}`,
                background: reason === option ? "var(--surface-subtle)" : "var(--surface)",
                borderRadius: 10,
                padding: "12px 14px",
                fontWeight: 600,
                fontSize: 14.5,
                color: "var(--ink)",
              }}
            >
              {t(`task.reason.${option}`)}
            </button>
          ))}
        </div>

        {reason === "other" ? (
          <input
            style={inputStyle}
            placeholder={t("task.reasonDetail")}
            value={detail}
            onChange={(event) => setDetail(event.target.value)}
          />
        ) : null}

        <div style={{ display: "flex", gap: 10 }}>
          <Button
            onClick={submit}
            disabled={busy || (reason === "other" && detail.trim() === "")}
            style={{ flex: 1 }}
          >
            {t("task.notDone")}
          </Button>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
        </div>
      </div>
    </div>
  );
}
