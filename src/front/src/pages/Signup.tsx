import { useState } from "react";
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button, Card, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "../components/ui";
import { useSession } from "../hooks/useSession";
import "../styles/signup.css";

const WHATSAPP_URL =
  "https://wa.me/5561983031823?text=Ol%C3%A1%21%20Quero%20saber%20mais%20sobre%20o%20Plant%C3%A3oVet";

const PROMISES = ["onTime", "overdue", "handover"] as const;
const STEPS = [1, 2, 3] as const;
const FAQS = ["card", "after", "data", "size"] as const;

// Toda seção de conteúdo (abaixo do hero) usa a mesma largura e o mesmo
// respiro vertical — só o hero tem layout próprio, porque divide a tela com
// o formulário.
const sectionStyle: CSSProperties = {
  maxWidth: 860,
  margin: "0 auto",
  padding: "clamp(32px, 6vw, 64px) clamp(16px, 5vw, 32px)",
  display: "grid",
  gap: 20,
};

/** O check reusado do Login.tsx: mesmo traço, mesmo ícone, em todo o produto. */
function CheckIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6L9 17l-5-5" />
    </svg>
  );
}

/** A landing pública: quem chega por aqui nunca ouviu falar do produto.
 *
 *  O formulário divide a primeira tela com o pitch de propósito — pedir para
 *  rolar antes de agir é a conversão que se perde. Abaixo de 900px a tela
 *  empilha e o formulário vem primeiro, porque é a única parte com uma ação
 *  de verdade; o resto é o motivo para preenchê-lo. */
export function Signup() {
  const { t } = useTranslation();
  const { signupClinic } = useSession();
  const describeError = useApiErrorMessage();

  const [clinicName, setClinicName] = useState("");
  const [adminName, setAdminName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  // O e-mail repetido é o erro mais provável e o único com saída óbvia: em vez
  // de só a mensagem, a pessoa ganha o caminho para entrar.
  const [emailTaken, setEmailTaken] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setEmailTaken(false);
    setBusy(true);
    try {
      await signupClinic({
        clinic_name: clinicName.trim(),
        admin_name: adminName.trim(),
        email: email.trim(),
        password,
        phone: phone.trim() || undefined,
      });
      // Nada a fazer no sucesso: a sessão foi salva, o App re-renderiza e o
      // RoleHome manda o administrador para /internados.
    } catch (err) {
      if (err instanceof ApiError && err.code === "email_taken") setEmailTaken(true);
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <section className="signup-hero" style={{ background: "var(--surface)", borderBottom: "1px solid var(--line)" }}>
        <div className="signup-form-card">
          <Card style={{ borderRadius: "var(--radius-lg)", padding: 24 }}>
            <form onSubmit={submit} className="signup-form" noValidate>
              <h2 className="signup-form-title">{t("signup.form.title")}</h2>

              <ErrorBanner message={error} />
              {emailTaken ? (
                <p style={{ margin: 0, fontSize: 14 }}>
                  <Link to="/entrar">{t("signup.form.signIn")}</Link>
                </p>
              ) : null}

              <Field label={t("signup.form.clinicName")}>
                <input
                  style={inputStyle}
                  autoComplete="organization"
                  value={clinicName}
                  onChange={(e) => setClinicName(e.target.value)}
                  required
                  minLength={2}
                />
              </Field>
              <Field label={t("signup.form.adminName")}>
                <input
                  style={inputStyle}
                  autoComplete="name"
                  value={adminName}
                  onChange={(e) => setAdminName(e.target.value)}
                  required
                  minLength={2}
                />
              </Field>
              <Field label={t("signup.form.email")}>
                <input
                  style={inputStyle}
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </Field>
              <Field label={t("signup.form.password")}>
                <input
                  style={inputStyle}
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  minLength={8}
                />
                <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                  {t("signup.form.passwordHint")}
                </span>
              </Field>
              <Field label={t("signup.form.phone")}>
                <input
                  style={inputStyle}
                  type="tel"
                  autoComplete="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                />
              </Field>

              <Button type="submit" disabled={busy}>
                {busy ? t("signup.form.busy") : t("signup.form.submit")}
              </Button>

              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)", textAlign: "center" }}>
                {t("signup.form.hasAccount")} <Link to="/entrar">{t("signup.form.signIn")}</Link>
              </p>
            </form>
          </Card>
        </div>

        <div className="signup-pitch">
          <div style={{ fontFamily: "'Bricolage Grotesque', system-ui", fontWeight: 800, fontSize: 22, color: "var(--primary)" }}>
            {t("signup.brand")}
          </div>
          <h1 style={{ fontSize: "clamp(1.9rem, 4vw, 2.75rem)", lineHeight: 1.15, marginTop: 16 }}>
            {t("signup.hero.title")}
          </h1>
          <p style={{ fontSize: 18, color: "var(--ink-2)", marginTop: 14, maxWidth: "52ch" }}>
            {t("signup.hero.subtitle")}
          </p>
          <p style={{ fontSize: 14, color: "var(--ink-3)", marginTop: 22, fontWeight: 600 }}>
            {t("signup.hero.trustline")}
          </p>
        </div>
      </section>

      <section style={sectionStyle}>
        <h2>{t("signup.problem.title")}</h2>
        <p style={{ margin: 0, maxWidth: "60ch", color: "var(--ink-2)", fontSize: 16, lineHeight: 1.6 }}>
          {t("signup.problem.body")}
        </p>
      </section>

      <section style={sectionStyle}>
        <h2>{t("signup.promises.title")}</h2>
        <div className="signup-promises-grid">
          {PROMISES.map((key) => (
            <Card key={key} style={{ display: "grid", gap: 10, alignContent: "start" }}>
              <span style={{ color: "var(--primary)" }}>
                <CheckIcon />
              </span>
              <h3 style={{ fontSize: 17 }}>{t(`signup.promise.${key}.title`)}</h3>
              <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14.5, lineHeight: 1.55 }}>
                {t(`signup.promise.${key}.body`)}
              </p>
            </Card>
          ))}
        </div>
      </section>

      <section style={sectionStyle}>
        <h2>{t("signup.how.title")}</h2>
        <div className="signup-how-grid">
          {STEPS.map((step) => (
            <div key={step} style={{ display: "grid", gap: 8 }}>
              <span
                className="tabular"
                style={{
                  fontFamily: "'Bricolage Grotesque', system-ui",
                  fontWeight: 800,
                  fontSize: 28,
                  color: "var(--primary)",
                }}
              >
                {step}
              </span>
              <h3 style={{ fontSize: 17 }}>{t(`signup.how.step${step}.title`)}</h3>
              <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14.5, lineHeight: 1.55 }}>
                {t(`signup.how.step${step}.body`)}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section style={sectionStyle}>
        <h2>{t("signup.faq.title")}</h2>
        <div className="signup-faq-list">
          {FAQS.map((key) => (
            <details
              key={key}
              style={{
                border: "1px solid var(--line)",
                borderRadius: "var(--radius)",
                padding: "14px 16px",
                background: "var(--surface)",
              }}
            >
              <summary style={{ cursor: "pointer", fontWeight: 600, fontSize: 15 }}>
                {t(`signup.faq.${key}.q`)}
              </summary>
              <p style={{ margin: "10px 0 0", color: "var(--ink-2)", fontSize: 14.5, lineHeight: 1.6 }}>
                {t(`signup.faq.${key}.a`)}
              </p>
            </details>
          ))}
        </div>
      </section>

      <footer
        style={{
          borderTop: "1px solid var(--line)",
          padding: "24px 20px",
          display: "flex",
          gap: 24,
          justifyContent: "center",
          flexWrap: "wrap",
          fontSize: 14,
        }}
      >
        <a
          href={WHATSAPP_URL}
          target="_blank"
          rel="noreferrer"
          style={{ color: "var(--primary)", fontWeight: 600, textDecoration: "none" }}
        >
          {t("signup.footer.talk")}
        </a>
        <Link to="/entrar" style={{ color: "var(--ink-2)" }}>
          {t("signup.footer.signIn")}
        </Link>
      </footer>
    </div>
  );
}
