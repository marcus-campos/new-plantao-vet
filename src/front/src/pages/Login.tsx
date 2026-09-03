import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { Button, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "../components/ui";
import { useSession } from "../hooks/useSession";

const CLAIMS = ["login.claim.onTime", "login.claim.overdue", "login.claim.handover"] as const;

export function Login() {
  const { t } = useTranslation();
  const { loginPersonal, loginPlatform, enrollStation, loginWithDevice, device, forgetDevice } =
    useSession();
  const describeError = useApiErrorMessage();

  const [mode, setMode] = useState<"personal" | "station" | "platform">("personal");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Vazio: o slug da clínica de demonstração vinha pré-preenchido para todo
  // mundo, inclusive em produção.
  const [clinicSlug, setClinicSlug] = useState(device?.clinicSlug ?? "");
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "personal") await loginPersonal(email, password);
      else if (mode === "platform") await loginPlatform(email, password);
      else if (device) await loginWithDevice();
      else await enrollStation(clinicSlug, code, deviceName.trim());
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-layout">
      <aside className="login-aside">
        <div style={{ fontFamily: "'Bricolage Grotesque', system-ui", fontWeight: 800, fontSize: 22 }}>
          Plantão<span style={{ opacity: 0.75 }}>Vet</span>
        </div>
        <h1 style={{ fontSize: "clamp(1.6rem, 3vw, 2.2rem)", lineHeight: 1.2, color: "#fff" }}>
          {t("login.title")}
        </h1>
        <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 12 }}>
          {CLAIMS.map((claim) => (
            <li key={claim} style={{ display: "flex", gap: 10, alignItems: "center", fontSize: 15 }}>
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M20 6L9 17l-5-5" />
              </svg>
              {t(claim)}
            </li>
          ))}
        </ul>
      </aside>

      <main className="login-main">
        <form onSubmit={submit} style={{ display: "grid", gap: 16, width: "min(400px, 100%)" }}>
          <div style={{ display: mode === "platform" ? "none" : "flex", gap: 8 }}>
            {(["personal", "station"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setMode(option)}
                style={{
                  flex: 1,
                  border: "1px solid var(--line)",
                  background: mode === option ? "var(--primary)" : "var(--surface)",
                  color: mode === option ? "#fff" : "var(--ink-2)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  fontWeight: 600,
                  fontSize: 14,
                }}
              >
                {t(`login.tab.${option}`)}
              </button>
            ))}
          </div>

          <ErrorBanner message={error} />

          {mode === "platform" ? (
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
              {t("login.platformHint")}
            </p>
          ) : null}

          {mode === "personal" || mode === "platform" ? (
            <>
              <Field label={t("login.email")}>
                <input
                  style={inputStyle}
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </Field>
              <Field label={t("login.password")}>
                <input
                  style={inputStyle}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </Field>
            </>
          ) : (
            <>
              {/* Aparelho já liberado não pergunta nada: o segredo é dele e
                  está guardado aqui. A chave de estação obrigava a redigitar,
                  a cada 12 horas, um segredo que circulava pela clínica. */}
              {device ? (
                <div className="device-known">
                  <span className="eyebrow">{t("login.thisDevice")}</span>
                  <strong>{device.deviceName}</strong>
                  <span className="device-known-clinic">{device.clinicSlug}</span>
                  <button
                    type="button"
                    className="device-forget"
                    onClick={() => {
                      forgetDevice();
                      setError(null);
                    }}
                  >
                    {t("login.notThisDevice")}
                  </button>
                </div>
              ) : (
                <>
                  <Field label={t("login.clinicSlug")}>
                    <input
                      style={inputStyle}
                      value={clinicSlug}
                      onChange={(e) => setClinicSlug(e.target.value)}
                      required
                    />
                  </Field>
                  <Field label={t("login.deviceCode")}>
                    <input
                      style={inputStyle}
                      className="tabular"
                      inputMode="numeric"
                      autoComplete="one-time-code"
                      maxLength={6}
                      placeholder="000000"
                      value={code}
                      onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                      required
                    />
                  </Field>
                  <Field label={t("login.deviceName")}>
                    <input
                      style={inputStyle}
                      value={deviceName}
                      placeholder={t("login.deviceNamePlaceholder")}
                      onChange={(e) => setDeviceName(e.target.value)}
                      required
                    />
                  </Field>
                  <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                    {t("login.stationHint")}
                  </p>
                </>
              )}
            </>
          )}

          {/* A porta de quem vende e dá suporte. Discreta de propósito: 99% de
              quem abre esta tela trabalha numa clínica. */}
          <button
            type="button"
            className="login-platform-link"
            onClick={() => {
              setMode(mode === "platform" ? "personal" : "platform");
              setError(null);
            }}
          >
            {mode === "platform" ? t("login.backToClinic") : t("login.platformLink")}
          </button>

          <Button type="submit" disabled={busy} style={{ padding: "13px 18px" }}>
            {busy
              ? t("common.loading")
              : mode === "station" && device
                ? t("login.enterAs", { device: device.deviceName })
                : t("login.submit")}
          </Button>

          <p style={{ margin: 0, fontSize: 14, color: "var(--ink-3)", textAlign: "center" }}>
            {t("login.noAccount")} <Link to="/">{t("login.createAccount")}</Link>
          </p>
        </form>
      </main>
    </div>
  );
}
