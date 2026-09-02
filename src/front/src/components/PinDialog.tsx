import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button, ErrorBanner, useApiErrorMessage } from "./ui";
import { useSession } from "../hooks/useSession";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

/** Seis dígitos: com quatro são 10 mil combinações, e o PIN é único por
 *  clínica. Em algumas centenas de pessoas a colisão vira o caso comum. */
const PIN_LENGTH = 6;
/** Quem já tinha um PIN de quatro continua entrando com ele até trocar: aí o
 *  envio não pode esperar o sexto dígito, e o botão faz esse papel. */
const PIN_MIN = 4;

/** Modo estação: identifica quem executa antes de gravar o ato clínico. */
export function PinDialog({
  context,
  onDone,
  onCancel,
}: {
  context?: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const { identifyOperator } = useSession();
  const describeError = useApiErrorMessage();
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(value: string) {
    setBusy(true);
    setError(null);
    try {
      await identifyOperator(value);
      onDone();
    } catch (err) {
      setError(describeError(err));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  function press(key: string) {
    if (key === "⌫") {
      setPin((current) => current.slice(0, -1));
      return;
    }
    if (!key || pin.length >= PIN_LENGTH) return;
    const next = pin + key;
    setPin(next);
    if (next.length === PIN_LENGTH) void submit(next);
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card" style={{ maxWidth: 340 }}>
        <h2 style={{ fontSize: 20 }}>{t("pin.title")}</h2>
        {context ? (
          <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-3)" }}>{context}</p>
        ) : null}

        <div style={{ display: "flex", gap: 12, justifyContent: "center", padding: "8px 0" }}>
          {Array.from({ length: PIN_LENGTH }, (_, index) => index).map((index) => (
            <div
              key={index}
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: index < pin.length ? "var(--primary)" : "var(--line)",
              }}
            />
          ))}
        </div>

        <ErrorBanner message={error} />

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
          {KEYS.map((key, index) => (
            <button
              key={index}
              type="button"
              disabled={busy || !key}
              onClick={() => press(key)}
              style={{
                minHeight: 60,
                border: "1px solid var(--line)",
                background: key ? "var(--surface)" : "transparent",
                borderColor: key ? "var(--line)" : "transparent",
                borderRadius: 10,
                fontSize: 20,
                fontWeight: 600,
                color: "var(--ink)",
              }}
            >
              {key}
            </button>
          ))}
        </div>

        <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>{t("pin.hint")}</p>
        {/* Seis dígitos enviam sozinhos. O botão existe para o PIN antigo de
            quatro, que ainda entra até a pessoa trocar. */}
        {pin.length >= PIN_MIN && pin.length < PIN_LENGTH ? (
          <Button disabled={busy} onClick={() => void submit(pin)}>
            {t("pin.submit")}
          </Button>
        ) : null}
        <Button variant="secondary" onClick={onCancel}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}
