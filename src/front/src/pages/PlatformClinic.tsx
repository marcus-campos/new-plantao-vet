import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Plan, PlatformClinic as ClinicDetail, PlatformMember, SubscriptionStatus } from "../api/types";
import { AdminModal, AdminNote, initials, license } from "../components/AdminShared";
import { Combobox } from "../components/Combobox";
import {
  Button,
  Card,
  ErrorBanner,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { StatusBadge, describePlan } from "./Platform";

const STATUSES: SubscriptionStatus[] = ["trial", "active", "past_due", "suspended", "cancelled"];

/** A ficha de uma clínica para quem dá suporte.
 *
 *  Três blocos, na ordem em que o telefone toca: quem é e como falar; a
 *  assinatura (o que o suporte pode mudar); e a equipe, com as duas ações que
 *  respondem 90% das ligações: "esqueci a senha" e "esqueci o PIN".
 *
 *  Nada aqui entra na clínica como se fosse alguém de lá. Não há
 *  "entrar como": ver a ficha é ver a ficha, e tudo o que muda fica na trilha
 *  da clínica com o nome do operador e o prefixo "Suporte". */
export function PlatformClinic() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [clinic, setClinic] = useState<ClinicDetail | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reset, setReset] = useState<{ member: PlatformMember; password: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setError(null);
    try {
      const [detail, catalogue] = await Promise.all([api.platformClinic(id), api.platformPlans()]);
      setClinic(detail);
      setPlans(catalogue);
    } catch (err) {
      setError(describeError(err));
    }
  }, [id, describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function patch(body: Record<string, unknown>) {
    if (!id) return;
    setBusy(true);
    setSaveError(null);
    try {
      setClinic(await api.platformUpdateClinic(id, body));
      setNotice(t("platform.saved"));
    } catch (err) {
      setSaveError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword(member: PlatformMember) {
    if (!id) return;
    if (!window.confirm(t("platform.confirmResetPassword", { name: member.name }))) return;
    setBusy(true);
    try {
      const result = await api.platformResetPassword(id, member.membership_id);
      setReset({ member, password: result.temporary_password });
      await load();
    } catch (err) {
      setSaveError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resetTour(member: PlatformMember) {
    if (!id) return;
    setBusy(true);
    try {
      await api.platformResetTour(id, member.membership_id);
      setNotice(t("platform.tourReset", { name: member.name }));
      await load();
    } catch (err) {
      setSaveError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  async function resetPin(member: PlatformMember) {
    if (!id) return;
    if (!window.confirm(t("platform.confirmResetPin", { name: member.name }))) return;
    setBusy(true);
    try {
      await api.platformResetPin(id, member.membership_id);
      setNotice(t("platform.pinReset", { name: member.name }));
      await load();
    } catch (err) {
      setSaveError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (error) return <div className="page"><ErrorState message={error} onRetry={() => void load()} /></div>;
  if (!clinic) return <div className="page"><Skeleton rows={4} height={90} /></div>;

  const now = Date.now();

  return (
    <div className="page">
      <Link to="/plataforma" className="patient-back">
        ← {t("platform.clinics")}
      </Link>
      <header className="page-head">
        <div className="page-head-text">
          <span className="eyebrow">{clinic.slug}</span>
          <h1 className="page-title">{clinic.name}</h1>
          <p className="page-subtitle">
            <StatusBadge status={clinic.subscription_status} trialEndsAt={clinic.trial_ends_at} now={now} />
            {" · "}
            {t("platform.summary", {
              members: clinic.members,
              patients: clinic.active_hospitalizations,
            })}
          </p>
        </div>
      </header>

      {notice ? <AdminNote tone="neutral">{notice}</AdminNote> : null}
      <ErrorBanner message={saveError} />

      <div className="platform-layout">
        <div style={{ display: "grid", gap: 16 }}>
          <Section title={t("platform.contactTitle")}>
            <Card>
              <ContactForm clinic={clinic} busy={busy} onSave={patch} />
            </Card>
          </Section>

          <Section title={t("platform.subscriptionTitle")} hint={t("platform.subscriptionHint")}>
            <Card>
              <SubscriptionForm clinic={clinic} plans={plans} busy={busy} onSave={patch} />
            </Card>
          </Section>

          <Section title={t("platform.teamTitle")} hint={t("platform.teamHint")}>
            <Card style={{ display: "grid", gap: 4 }}>
              {clinic.members_list.map((member) => (
                <div key={member.membership_id} className="shift-person">
                  <span className="hv-avatar" aria-hidden="true">
                    {initials(member.name)}
                  </span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 600 }}>
                      {member.name}
                      {!member.is_active ? (
                        <span className="admin-badge admin-badge-off" style={{ marginLeft: 8 }}>
                          {t("team.inactive")}
                        </span>
                      ) : null}
                    </div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                      {t(`team.role.${member.role}`)} · {member.email}
                      {license(member) ? ` · ${license(member)}` : ""}
                      {member.has_pin ? "" : ` · ${t("platform.noPin")}`}
                    </div>
                  </div>
                  <div className="admin-toolbar">
                    <Button
                      variant="secondary"
                      disabled={busy}
                      onClick={() => void resetPassword(member)}
                      style={{ padding: "7px 12px", fontSize: 13 }}
                    >
                      {t("platform.resetPassword")}
                    </Button>
                    {member.has_pin ? (
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void resetPin(member)}
                        style={{ padding: "7px 12px", fontSize: 13 }}
                      >
                        {t("platform.resetPin")}
                      </Button>
                    ) : null}
                    {/* Só para quem já viu: reativar um tour que ainda vai
                        aparecer não faria nada, e um botão sem efeito é pior
                        que um botão ausente. */}
                    {member.tour_done ? (
                      <Button
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void resetTour(member)}
                        style={{ padding: "7px 12px", fontSize: 13 }}
                      >
                        {t("platform.resetTour")}
                      </Button>
                    ) : null}
                  </div>
                </div>
              ))}
            </Card>
          </Section>
        </div>

        <aside style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Card>
            <span className="hv-eyebrow">{t("platform.devicesTitle")}</span>
            {clinic.devices.length === 0 ? (
              <p className="hv-muted">{t("platform.noDevices")}</p>
            ) : (
              clinic.devices.map((device) => (
                <div key={device.id} className="platform-device">
                  <strong>{device.name}</strong>
                  <span className="admin-cell-muted">
                    {t(`devices.status.${device.status}`, { defaultValue: device.status })}
                    {device.pin_locked_at ? ` · ${t("platform.deviceLocked")}` : ""}
                  </span>
                </div>
              ))
            )}
          </Card>

          <Card>
            <span className="hv-eyebrow">{t("platform.activityTitle")}</span>
            {clinic.recent_audit.length === 0 ? (
              <p className="hv-muted">{t("platform.neverUsed")}</p>
            ) : (
              <ul className="platform-audit">
                {clinic.recent_audit.map((entry) => (
                  <li key={entry.id}>
                    <span className="tabular admin-cell-muted">
                      {new Date(entry.created_at).toLocaleString()}
                    </span>
                    <span>
                      <strong>{entry.actor_name}</strong> ·{" "}
                      {t(`audit.action.${entry.action}`, { defaultValue: entry.action })}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card>
            <span className="hv-eyebrow">{t("platform.notesTitle")}</span>
            <NotesForm clinic={clinic} busy={busy} onSave={patch} />
          </Card>
        </aside>
      </div>

      {reset ? (
        <AdminModal title={t("platform.resetDoneTitle", { name: reset.member.name })} onClose={() => setReset(null)}>
          <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{t("platform.resetDoneHint")}</p>
          <div className="platform-credentials">
            <span className="admin-section-label">{t("login.email")}</span>
            <div className="admin-secret">{reset.member.email}</div>
            <span className="admin-section-label">{t("platform.temporaryPassword")}</span>
            <div className="admin-secret">{reset.password}</div>
          </div>
          <AdminNote>{t("platform.passwordOnce")}</AdminNote>
          <Button onClick={() => setReset(null)}>{t("common.done")}</Button>
        </AdminModal>
      ) : null}
    </div>
  );
}

function ContactForm({ clinic, busy, onSave }: { clinic: ClinicDetail; busy: boolean; onSave: (b: Record<string, unknown>) => Promise<void> }) {
  const { t } = useTranslation();
  const [name, setName] = useState(clinic.contact_name ?? "");
  const [email, setEmail] = useState(clinic.contact_email ?? "");
  const [phone, setPhone] = useState(clinic.contact_phone ?? "");
  const dirty =
    name !== (clinic.contact_name ?? "") ||
    email !== (clinic.contact_email ?? "") ||
    phone !== (clinic.contact_phone ?? "");
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="form-grid-3">
        <Field label={t("platform.form.contactName")}>
          <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label={t("platform.form.contactEmail")}>
          <input style={inputStyle} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label={t("platform.form.contactPhone")}>
          <input style={inputStyle} inputMode="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </Field>
      </div>
      <p className="hv-muted" style={{ margin: 0 }}>
        {t("platform.regional", { locale: clinic.locale, timezone: clinic.timezone, currency: clinic.currency })}
      </p>
      {dirty ? (
        <div className="admin-toolbar">
          <Button
            disabled={busy}
            onClick={() =>
              void onSave({
                contact_name: name.trim() || null,
                contact_email: email.trim() || null,
                contact_phone: phone.trim() || null,
              })
            }
          >
            {t("common.save")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** O que o suporte pode mudar na assinatura, e o que cada mudança faz.
 *
 *  Suspender fecha a porta NO LOGIN, nunca no meio da sessão: uma clínica com
 *  paciente internado não perde a prescrição por causa de boleto. Quem já
 *  está dentro termina o turno; quem chega depois vê o motivo. A tela diz
 *  isso antes do clique, porque é a única ação daqui que o cliente sente. */
function SubscriptionForm({ clinic, plans, busy, onSave }: { clinic: ClinicDetail; plans: Plan[]; busy: boolean; onSave: (b: Record<string, unknown>) => Promise<void> }) {
  const { t } = useTranslation();
  const [plan, setPlan] = useState(clinic.plan_tier ?? "");
  // O plano atual aparece mesmo se aposentado (a clínica está nele); os
  // outros só se ativos: aposentado não recebe clínica nova.
  const options = plans.filter((item) => item.is_active || item.code === clinic.plan_tier);
  const [beds, setBeds] = useState(clinic.bed_limit === null ? "" : String(clinic.bed_limit));
  const [status, setStatus] = useState<SubscriptionStatus>(clinic.subscription_status);
  const [trialEnds, setTrialEnds] = useState(clinic.trial_ends_at ? clinic.trial_ends_at.slice(0, 10) : "");

  const dirty =
    plan !== (clinic.plan_tier ?? "") ||
    beds !== (clinic.bed_limit === null ? "" : String(clinic.bed_limit)) ||
    status !== clinic.subscription_status ||
    trialEnds !== (clinic.trial_ends_at ? clinic.trial_ends_at.slice(0, 10) : "");
  const closing = (status === "suspended" || status === "cancelled") &&
    clinic.subscription_status !== "suspended" && clinic.subscription_status !== "cancelled";

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <div className="form-grid-3">
        <Field label={t("platform.form.plan")}>
          <Combobox
            value={plan}
            onChange={(value) => {
              setPlan(value);
              // Trocar de plano preenche o limite do plano: é o caso comum.
              const next = plans.find((item) => item.code === value);
              if (next) setBeds(next.bed_limit === null ? "" : String(next.bed_limit));
            }}
            options={options.map((item) => ({
              value: item.code,
              label: item.is_active ? item.name : t("platform.retiredPlanLabel", { name: item.name }),
              hint: describePlan(item, t),
            }))}
          />
        </Field>
        <Field label={t("platform.form.beds")}>
          <input style={inputStyle} className="tabular" inputMode="numeric" value={beds} onChange={(e) => setBeds(e.target.value.replace(/\D/g, ""))} />
          <span className="dose-hint">{t("platform.form.bedsHint")}</span>
        </Field>
        <Field label={t("platform.form.status")}>
          <Combobox
            value={status}
            onChange={(value) => setStatus(value as SubscriptionStatus)}
            options={STATUSES.map((s) => ({ value: s, label: t(`platform.status.${s}`) }))}
          />
        </Field>
      </div>
      {status === "trial" ? (
        <Field label={t("platform.form.trialEnds")}>
          <input type="date" style={{ ...inputStyle, width: 200 }} value={trialEnds} onChange={(e) => setTrialEnds(e.target.value)} />
        </Field>
      ) : null}
      {closing ? <AdminNote tone="danger">{t("platform.closingWarning")}</AdminNote> : null}
      {clinic.suspended_at ? (
        <p className="hv-muted" style={{ margin: 0 }}>
          {t("platform.suspendedSince", { when: new Date(clinic.suspended_at).toLocaleString() })}
        </p>
      ) : null}
      {dirty ? (
        <div className="admin-toolbar">
          <Button
            disabled={busy}
            onClick={() =>
              void onSave({
                ...(plan !== (clinic.plan_tier ?? "") ? { plan_tier: plan } : {}),
                bed_limit: beds === "" ? null : Number(beds),
                subscription_status: status,
                trial_ends_at: status === "trial" && trialEnds ? `${trialEnds}T23:59:59Z` : null,
              })
            }
          >
            {t("common.save")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function NotesForm({ clinic, busy, onSave }: { clinic: ClinicDetail; busy: boolean; onSave: (b: Record<string, unknown>) => Promise<void> }) {
  const { t } = useTranslation();
  const [notes, setNotes] = useState(clinic.support_notes ?? "");
  const dirty = notes !== (clinic.support_notes ?? "");
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <textarea
        style={{ ...inputStyle, minHeight: 120, resize: "vertical" }}
        value={notes}
        placeholder={t("platform.notesPlaceholder")}
        onChange={(e) => setNotes(e.target.value)}
      />
      {dirty ? (
        <Button variant="secondary" disabled={busy} onClick={() => void onSave({ support_notes: notes.trim() || null })}>
          {t("common.save")}
        </Button>
      ) : null}
    </div>
  );
}
