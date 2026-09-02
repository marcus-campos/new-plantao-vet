import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { AdminModal, AdminNote } from "./AdminShared";
import { Button, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "./ui";

const PIN_LENGTH = 6;

/** Trocar o próprio PIN.
 *
 *  Existia só o caminho do administrador definir o PIN de outra pessoa. Para
 *  trocar um PIN que alguém viu por cima do ombro, era preciso pedir a um
 *  terceiro: o incentivo era não trocar, e um PIN que ninguém troca deixa de
 *  identificar quem executou o ato clínico, que é a única coisa que ele faz.
 *
 *  `hasPin` decide se o PIN atual é pedido. Quem ainda não tem nenhum não tem
 *  o que confirmar, e exigir um valor inexistente deixaria a pessoa presa.
 */
export function MyPinDialog({ hasPin, onClose }: { hasPin: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const mismatch = confirm.length === PIN_LENGTH && confirm !== next;
  const ready =
    next.length === PIN_LENGTH && confirm === next && (!hasPin || current.length >= 4);

  function digits(value: string): string {
    return value.replace(/\D/g, "").slice(0, PIN_LENGTH);
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api.changeMyPin(hasPin ? current : null, next);
      setSaved(true);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    return (
      <AdminModal title={t("mypin.title")} onClose={onClose}>
        <AdminNote tone="neutral">{t("mypin.saved")}</AdminNote>
        <Button onClick={onClose}>{t("common.done")}</Button>
      </AdminModal>
    );
  }

  return (
    <AdminModal title={t("mypin.title")} onClose={onClose}>
      <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-3)" }}>{t("mypin.hint")}</p>
      {!hasPin ? <AdminNote>{t("mypin.firstTime")}</AdminNote> : null}

      <ErrorBanner message={error} />

      {hasPin ? (
        <Field label={t("mypin.current")}>
          <input
            style={inputStyle}
            className="tabular"
            type="password"
            inputMode="numeric"
            autoComplete="current-password"
            value={current}
            onChange={(event) => setCurrent(digits(event.target.value))}
          />
        </Field>
      ) : null}

      <Field label={t("mypin.new")}>
        <input
          style={inputStyle}
          className="tabular"
          type="password"
          inputMode="numeric"
          autoComplete="new-password"
          value={next}
          onChange={(event) => setNext(digits(event.target.value))}
        />
      </Field>

      <Field label={t("mypin.confirm")}>
        <input
          style={inputStyle}
          className="tabular"
          type="password"
          inputMode="numeric"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(digits(event.target.value))}
        />
        {/* Um PIN digitado errado nas duas vezes tranca a pessoa fora do
            próprio aparelho, e ela só descobre no plantão seguinte. */}
        {mismatch ? <span className="dose-hint">{t("mypin.mismatch")}</span> : null}
      </Field>

      <div className="admin-toolbar">
        <Button disabled={!ready || busy} onClick={() => void save()}>
          {t("mypin.save")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
    </AdminModal>
  );
}
