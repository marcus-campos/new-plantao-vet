import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes } from "react-router-dom";

import { api } from "../api/client";
import type { Plan, PlatformClinicRow, SubscriptionStatus } from "../api/types";
import { AdminModal, AdminNote } from "../components/AdminShared";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useSession } from "../hooks/useSession";
import { PlatformClinic } from "./PlatformClinic";
import { PlatformPlans } from "./PlatformPlans";
import "../styles/admin.css";
import "../styles/platform.css";

/** O back-office: quem vende, faz onboarding e dá suporte.
 *
 *  Não é uma aba da clínica. É outra porta, com outro token, fora de qualquer
 *  clínica: o operador da plataforma não é membro de nenhuma, e tudo o que
 *  ele faz numa clínica fica na trilha DELA, com o nome dele e o prefixo
 *  "Suporte". O cliente vê que o suporte entrou, e vê o quê.
 *
 *  A lista responde as três perguntas de quem vende, e só elas: tem gente?
 *  tem paciente? ainda usa? A terceira é a que separa o cliente ativo do que
 *  vai cancelar, e é a que nenhuma tela de clínica consegue responder. */
export function PlatformApp() {
  const { t } = useTranslation();
  const { platformUser, logout } = useSession();

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/plataforma" className="brand">
          Plantão<em>Vet</em>
          <span className="platform-tag">{t("platform.tag")}</span>
        </Link>
        <nav className="nav-primary" aria-label={t("platform.tag")}>
          <Link to="/plataforma" className="nav-link">
            {t("platform.clinics")}
          </Link>
          <Link to="/plataforma/planos" className="nav-link">
            {t("platform.plans")}
          </Link>
        </nav>
        <div className="identity">
          {platformUser ? <span className="identity-chip">{platformUser.name}</span> : null}
          <button type="button" className="nav-link" onClick={logout}>
            {t("nav.logout")}
          </button>
        </div>
      </header>
      <main className="app-main">
        <Routes>
          <Route path="/plataforma" element={<Clinics />} />
          <Route path="/plataforma/clinicas/:id" element={<PlatformClinic />} />
          <Route path="/plataforma/planos" element={<PlatformPlans />} />
          <Route path="*" element={<Navigate to="/plataforma" replace />} />
        </Routes>
      </main>
    </div>
  );
}

/** A ordem da lista é a ordem do trabalho: quem precisa de atenção primeiro.
 *  Atrasado antes de tudo; depois teste acabando; depois quem parou de usar. */
function urgency(row: PlatformClinicRow, now: number): number {
  if (row.subscription_status === "past_due") return 0;
  if (row.subscription_status === "trial") {
    const ends = row.trial_ends_at ? Date.parse(row.trial_ends_at) : now;
    if (ends - now < 7 * 86_400_000) return 1;
  }
  if (row.last_activity_at && now - Date.parse(row.last_activity_at) > 7 * 86_400_000) return 2;
  if (row.subscription_status === "suspended" || row.subscription_status === "cancelled") return 5;
  return 3;
}

function Clinics() {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [rows, setRows] = useState<PlatformClinicRow[] | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setError(null);
    try {
      const [clinics, catalogue] = await Promise.all([api.platformClinics(), api.platformPlans()]);
      setRows(clinics);
      setPlans(catalogue);
    } catch (err) {
      setError(describeError(err));
    }
  }, [describeError]);

  const planNames = useMemo(
    () => Object.fromEntries(plans.map((plan) => [plan.code, plan.name])),
    [plans],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const now = Date.now();
  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return [...(rows ?? [])]
      .filter(
        (row) =>
          !needle ||
          row.name.toLowerCase().includes(needle) ||
          row.slug.includes(needle) ||
          (row.contact_name ?? "").toLowerCase().includes(needle),
      )
      .sort((a, b) => urgency(a, now) - urgency(b, now) || a.name.localeCompare(b.name));
  }, [rows, search, now]);

  const totals = useMemo(() => {
    const list = rows ?? [];
    return {
      clinics: list.length,
      active: list.filter((r) => r.subscription_status === "active").length,
      trial: list.filter((r) => r.subscription_status === "trial").length,
      pastDue: list.filter((r) => r.subscription_status === "past_due").length,
      patients: list.reduce((sum, r) => sum + r.active_hospitalizations, 0),
    };
  }, [rows]);

  return (
    <div className="page">
      <Section
        title={t("platform.clinics")}
        hint={t("platform.clinicsHint")}
        actions={<Button onClick={() => setCreating(true)}>{t("platform.newClinic")}</Button>}
      >
        {/* Os números que abrem o dia de quem vende. Não é painel de KPI: é a
            resposta a "quantos pagam, quantos testam, quantos atrasaram". */}
        <div className="platform-stats">
          <Stat label={t("platform.stat.clinics")} value={totals.clinics} />
          <Stat label={t("platform.stat.active")} value={totals.active} tone="ok" />
          <Stat label={t("platform.stat.trial")} value={totals.trial} />
          <Stat label={t("platform.stat.pastDue")} value={totals.pastDue} tone={totals.pastDue ? "late" : undefined} />
          <Stat label={t("platform.stat.patients")} value={totals.patients} />
        </div>

        {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!error && rows === null ? <Skeleton rows={4} height={64} /> : null}

        {rows !== null && rows.length === 0 ? (
          <EmptyState
            title={t("platform.empty")}
            hint={t("platform.emptyHint")}
            action={<Button onClick={() => setCreating(true)}>{t("platform.newClinic")}</Button>}
          />
        ) : null}

        {rows !== null && rows.length > 0 ? (
          <Card style={{ display: "grid", gap: 12 }}>
            <input
              type="search"
              style={inputStyle}
              placeholder={t("platform.search")}
              aria-label={t("platform.search")}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>{t("platform.col.clinic")}</th>
                    <th>{t("platform.col.plan")}</th>
                    <th>{t("platform.col.status")}</th>
                    <th>{t("platform.col.team")}</th>
                    <th>{t("platform.col.patients")}</th>
                    <th>{t("platform.col.activity")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <ClinicRow key={row.id} row={row} now={now} planNames={planNames} />
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}
      </Section>

      {creating ? (
        <CreateClinicDialog
          plans={plans.filter((plan) => plan.is_active)}
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            void load();
          }}
        />
      ) : null}
    </div>
  );
}

/** "Até 25 leitos · R$ 497/mês" ou "Teste de 14 dias · até 10 leitos". */
export function describePlan(plan: Plan, t: (k: string, o?: Record<string, unknown>) => string): string {
  const beds = plan.bed_limit === null ? t("platform.unlimitedBeds") : t("platform.upToBeds", { count: plan.bed_limit });
  if (plan.trial_days > 0) return t("platform.trialPlanLine", { count: plan.trial_days, beds });
  const price = new Intl.NumberFormat("pt-BR", { style: "currency", currency: plan.currency }).format(plan.price_minor / 100);
  return t("platform.paidPlanLine", { beds, price });
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "ok" | "late" }) {
  return (
    <div className={`platform-stat${tone ? ` platform-stat-${tone}` : ""}`}>
      <span className="platform-stat-value tabular">{value}</span>
      <span className="platform-stat-label">{label}</span>
    </div>
  );
}

export function StatusBadge({ status, trialEndsAt, now }: { status: SubscriptionStatus; trialEndsAt: string | null; now: number }) {
  const { t } = useTranslation();
  const tone =
    status === "active" ? "on" : status === "past_due" ? "warn" : status === "trial" ? "" : "off";
  let label = t(`platform.status.${status}`);
  if (status === "trial" && trialEndsAt) {
    const days = Math.max(0, Math.ceil((Date.parse(trialEndsAt) - now) / 86_400_000));
    label = t("platform.trialDays", { count: days });
  }
  return <span className={`admin-badge${tone ? ` admin-badge-${tone}` : ""}`}>{label}</span>;
}

function ClinicRow({ row, now, planNames }: { row: PlatformClinicRow; now: number; planNames: Record<string, string> }) {
  const { t } = useTranslation();
  const silent = row.last_activity_at && now - Date.parse(row.last_activity_at) > 7 * 86_400_000;
  const days = row.last_activity_at
    ? Math.floor((now - Date.parse(row.last_activity_at)) / 86_400_000)
    : null;
  return (
    <tr>
      <td>
        <Link to={`/plataforma/clinicas/${row.id}`} className="platform-clinic-link">
          <strong>{row.name}</strong>
          <span className="admin-cell-muted">
            {row.slug}
            {row.contact_name ? ` · ${row.contact_name}` : ""}
          </span>
        </Link>
      </td>
      <td>
        {row.plan_tier ? (planNames[row.plan_tier] ?? row.plan_tier) : "–"}
        {row.bed_limit !== null ? (
          <span className="admin-cell-muted"> · {t("platform.beds", { count: row.bed_limit })}</span>
        ) : null}
      </td>
      <td>
        <StatusBadge status={row.subscription_status} trialEndsAt={row.trial_ends_at} now={now} />
      </td>
      <td className="tabular">{row.members}</td>
      <td className="tabular">{row.active_hospitalizations}</td>
      <td className={silent ? "platform-silent" : "admin-cell-muted"}>
        {days === null
          ? t("platform.neverUsed")
          : days === 0
            ? t("platform.today")
            : t("platform.daysAgo", { count: days })}
      </td>
    </tr>
  );
}

/** Onboarding: a clínica e o primeiro administrador, num ato só.
 *
 *  Pede só o que o vendedor sabe na hora: nome da clínica, quem é o
 *  responsável, e-mail. Plano começa em teste de 30 dias, que é a proposta
 *  comercial. A senha é sorteada e mostrada UMA vez: é o que se entrega ao
 *  cliente no telefone ou por mensagem. */
function CreateClinicDialog({ plans, onClose, onCreated }: { plans: Plan[]; onClose: () => void; onCreated: () => void }) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [plan, setPlan] = useState(plans[0]?.code ?? "");
  const [contactName, setContactName] = useState("");
  const [contactPhone, setContactPhone] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<{ email: string; password: string; name: string } | null>(null);

  // O slug nasce do nome e para de acompanhar quando alguém mexe nele.
  function suggestSlug(value: string) {
    return value
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40);
  }

  const chosen = plans.find((item) => item.code === plan) ?? null;
  const ready =
    plan !== "" &&
    name.trim().length >= 2 && /^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$/.test(slug) &&
    adminName.trim().length >= 2 && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(adminEmail);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.platformCreateClinic({
        name: name.trim(),
        slug,
        plan_tier: plan,
        contact_name: contactName.trim() || adminName.trim(),
        contact_phone: contactPhone.trim() || null,
        contact_email: adminEmail.trim(),
        admin_name: adminName.trim(),
        admin_email: adminEmail.trim(),
      });
      setDone({ email: result.admin_email, password: result.admin_password, name: result.clinic.name });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AdminModal title={t("platform.createdTitle", { name: done.name })} onClose={onCreated}>
        <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{t("platform.createdHint")}</p>
        <div className="platform-credentials">
          <span className="admin-section-label">{t("login.email")}</span>
          <div className="admin-secret">{done.email}</div>
          <span className="admin-section-label">{t("login.password")}</span>
          <div className="admin-secret">{done.password}</div>
        </div>
        <AdminNote>{t("platform.passwordOnce")}</AdminNote>
        <Button onClick={onCreated}>{t("common.done")}</Button>
      </AdminModal>
    );
  }

  return (
    <AdminModal title={t("platform.newClinic")} onClose={onClose} wide>
      <ErrorBanner message={error} />
      <Field label={t("platform.form.name")}>
        <input
          style={inputStyle}
          value={name}
          autoFocus
          placeholder={t("platform.form.namePlaceholder")}
          onChange={(event) => {
            setName(event.target.value);
            if (!slugTouched) setSlug(suggestSlug(event.target.value));
          }}
        />
      </Field>
      <div className="form-grid-2">
        <Field label={t("platform.form.slug")}>
          <input
            style={inputStyle}
            className="tabular"
            value={slug}
            onChange={(event) => {
              setSlugTouched(true);
              setSlug(suggestSlug(event.target.value));
            }}
          />
          <span className="dose-hint">{t("platform.form.slugHint")}</span>
        </Field>
        <Field label={t("platform.form.plan")}>
          {/* Só planos ATIVOS: um plano aposentado não recebe clínica nova. */}
          <div className="chip-group">
            {plans.map((option) => (
              <button
                key={option.code}
                type="button"
                aria-pressed={plan === option.code}
                className={plan === option.code ? "chip chip-on" : "chip"}
                onClick={() => setPlan(option.code)}
              >
                {option.name}
              </button>
            ))}
          </div>
          {chosen ? <span className="dose-hint">{describePlan(chosen, t)}</span> : null}
          {plans.length === 0 ? <AdminNote tone="danger">{t("platform.noActivePlans")}</AdminNote> : null}
        </Field>
      </div>

      <h3 style={{ fontSize: 15, marginTop: 6 }}>{t("platform.form.adminTitle")}</h3>
      <div className="form-grid-2">
        <Field label={t("platform.form.adminName")}>
          <input style={inputStyle} value={adminName} onChange={(event) => setAdminName(event.target.value)} />
        </Field>
        <Field label={t("platform.form.adminEmail")}>
          <input
            style={inputStyle}
            type="email"
            value={adminEmail}
            onChange={(event) => setAdminEmail(event.target.value)}
          />
        </Field>
      </div>
      <div className="form-grid-2">
        <Field label={t("platform.form.contactName")}>
          <input
            style={inputStyle}
            value={contactName}
            placeholder={adminName || undefined}
            onChange={(event) => setContactName(event.target.value)}
          />
        </Field>
        <Field label={t("platform.form.contactPhone")}>
          <input
            style={inputStyle}
            inputMode="tel"
            value={contactPhone}
            onChange={(event) => setContactPhone(event.target.value)}
          />
        </Field>
      </div>
      <AdminNote tone="neutral">
        {chosen && chosen.trial_days > 0
          ? t("platform.form.trialPlanNote", { count: chosen.trial_days })
          : t("platform.form.trialNote")}
      </AdminNote>
      <div className="admin-toolbar">
        <Button disabled={!ready || busy} onClick={() => void submit()}>
          {t("platform.form.create")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
    </AdminModal>
  );
}
