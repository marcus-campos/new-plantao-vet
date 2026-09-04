import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ApiError } from "../api/client";
import { Button, ErrorBanner, Field, inputStyle, useApiErrorMessage } from "../components/ui";
import { useSession } from "../hooks/useSession";

/** O formulário que cria a clínica.
 *
 *  Extraído da página para poder aparecer duas vezes na landing — no alto e no
 *  fim — sem duplicar a lógica. Quem rolou a página inteira não deveria ter que
 *  rolar de volta para agir. */
export function SignupForm({ id }: { id?: string }) {
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
    <form onSubmit={submit} className="lp-form" id={id} noValidate>
      <h2 className="lp-form-title">{t("signup.form.title")}</h2>
      <p className="lp-form-sub">{t("signup.form.subtitle")}</p>

      {/* error.email_taken é compartilhado com a tela de suporte da plataforma
          (Platform.tsx), que não tem link nenhum — por isso o texto genérico
          não pode prometer "Entre por aqui". Aqui, com o link logo abaixo, a
          promessa é verdadeira. */}
      <ErrorBanner message={emailTaken ? t("signup.form.emailTaken") : error} />
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
        <span className="lp-hint">{t("signup.form.passwordHint")}</span>
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

      <p className="lp-form-foot">
        {t("signup.form.hasAccount")} <Link to="/entrar">{t("signup.form.signIn")}</Link>
      </p>
    </form>
  );
}
