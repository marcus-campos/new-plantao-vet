import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { Plan } from "../api/types";
import { AdminModal, AdminNote } from "../components/AdminShared";
import { Combobox } from "../components/Combobox";
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
import { describePlan } from "./Platform";

/** Os planos como dado.
 *
 *  Era um dicionário no código, e quem vende não muda código para lançar um
 *  plano. O ciclo de vida que um plano tem na prática está inteiro aqui:
 *  nasce, recebe clínicas enquanto ativo, aposenta (ninguém novo entra, quem
 *  está fica) e migra (todo mundo vai para outro, com uma entrada na trilha
 *  de cada clínica). "Fundador" não é um tipo especial: é um plano que um dia
 *  se aposenta com um clique, depois de migrar quem estava nele. */
export function PlatformPlans() {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [plans, setPlans] = useState<Plan[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<Plan | "new" | null>(null);
  const [migrating, setMigrating] = useState<Plan | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setPlans(await api.platformPlans());
    } catch (err) {
      setError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function act(action: () => Promise<void>) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  function retire(plan: Plan) {
    if (!window.confirm(t("platform.plan.confirmRetire", { name: plan.name, count: plan.clinics })))
      return;
    void act(() => api.platformUpdatePlan(plan.code, { is_active: false }).then(() => undefined));
  }

  function remove(plan: Plan) {
    if (!window.confirm(t("platform.plan.confirmDelete", { name: plan.name }))) return;
    void act(() => api.platformDeletePlan(plan.code));
  }

  const money = (plan: Plan) =>
    new Intl.NumberFormat("pt-BR", { style: "currency", currency: plan.currency }).format(
      plan.price_minor / 100,
    );

  return (
    <div className="page">
      <Section
        title={t("platform.plans")}
        hint={t("platform.plansHint")}
        actions={<Button onClick={() => setEditing("new")}>{t("platform.newPlan")}</Button>}
      >
        {notice ? <AdminNote tone="neutral">{notice}</AdminNote> : null}
        <ErrorBanner message={actionError} />
        {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!error && plans === null ? <Skeleton rows={3} height={56} /> : null}

        {plans !== null && plans.length === 0 ? (
          <EmptyState
            title={t("platform.plan.empty")}
            action={<Button onClick={() => setEditing("new")}>{t("platform.newPlan")}</Button>}
          />
        ) : null}

        {plans !== null && plans.length > 0 ? (
          <Card>
            <div className="admin-table-wrap">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>{t("platform.plan.col.plan")}</th>
                    <th>{t("platform.plan.col.beds")}</th>
                    <th>{t("platform.plan.col.price")}</th>
                    <th>{t("platform.plan.col.trial")}</th>
                    <th>{t("platform.plan.col.clinics")}</th>
                    <th>{t("platform.plan.col.state")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {plans.map((plan) => (
                    <tr key={plan.code} className={plan.is_active ? "" : "platform-plan-retired"}>
                      <td>
                        <strong>{plan.name}</strong>
                        <span className="admin-cell-muted"> · {plan.code}</span>
                        {plan.notes ? (
                          <div className="admin-cell-muted" style={{ fontSize: 12.5 }}>{plan.notes}</div>
                        ) : null}
                      </td>
                      <td className="tabular">
                        {plan.bed_limit === null ? t("platform.unlimitedBeds") : plan.bed_limit}
                      </td>
                      <td className="tabular">{money(plan)}</td>
                      <td>
                        {plan.trial_days > 0
                          ? t("platform.plan.trialDays", { count: plan.trial_days })
                          : t("platform.plan.paid")}
                      </td>
                      <td className="tabular">{plan.clinics}</td>
                      <td>
                        {plan.is_active ? (
                          <span className="admin-badge admin-badge-on">{t("platform.plan.active")}</span>
                        ) : (
                          <span className="admin-badge admin-badge-off">
                            {t("platform.plan.retired", {
                              when: plan.retired_at ? new Date(plan.retired_at).toLocaleDateString() : "",
                            })}
                          </span>
                        )}
                      </td>
                      <td>
                        <div className="admin-toolbar" style={{ justifyContent: "flex-end" }}>
                          <Button variant="secondary" disabled={busy} onClick={() => setEditing(plan)} style={{ padding: "7px 12px", fontSize: 13 }}>
                            {t("platform.plan.edit")}
                          </Button>
                          {plan.clinics > 0 ? (
                            <Button variant="secondary" disabled={busy} onClick={() => setMigrating(plan)} style={{ padding: "7px 12px", fontSize: 13 }}>
                              {t("platform.plan.migrate")}
                            </Button>
                          ) : null}
                          {plan.is_active ? (
                            <Button variant="secondary" disabled={busy} onClick={() => retire(plan)} style={{ padding: "7px 12px", fontSize: 13 }}>
                              {t("platform.plan.retire")}
                            </Button>
                          ) : (
                            <Button variant="secondary" disabled={busy} onClick={() => void act(() => api.platformUpdatePlan(plan.code, { is_active: true }).then(() => undefined))} style={{ padding: "7px 12px", fontSize: 13 }}>
                              {t("platform.plan.reactivate")}
                            </Button>
                          )}
                          {plan.clinics === 0 ? (
                            <Button variant="secondary" disabled={busy} onClick={() => remove(plan)} style={{ padding: "7px 12px", fontSize: 13 }}>
                              {t("platform.plan.delete")}
                            </Button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="admin-footnote">{t("platform.plan.retireHint")}</p>
          </Card>
        ) : null}
      </Section>

      {editing ? (
        <PlanDialog
          plan={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void load();
          }}
        />
      ) : null}

      {migrating && plans ? (
        <MigrateDialog
          source={migrating}
          targets={plans.filter((plan) => plan.is_active && plan.code !== migrating.code)}
          onClose={() => setMigrating(null)}
          onDone={(moved, to) => {
            setMigrating(null);
            setNotice(t("platform.plan.migrated", { count: moved, to }));
            void load();
          }}
        />
      ) : null}
    </div>
  );
}

function PlanDialog({ plan, onClose, onSaved }: { plan: Plan | null; onClose: () => void; onSaved: () => void }) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [code, setCode] = useState(plan?.code ?? "");
  const [name, setName] = useState(plan?.name ?? "");
  const [beds, setBeds] = useState(plan?.bed_limit === null || plan === null ? "" : String(plan.bed_limit));
  const [price, setPrice] = useState(plan ? (plan.price_minor / 100).toFixed(2).replace(".", ",") : "");
  const [trial, setTrial] = useState(plan ? String(plan.trial_days) : "0");
  const [notes, setNotes] = useState(plan?.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const priceMinor = (() => {
    const normalized = price.replace(/\s/g, "").replace(",", ".");
    if (normalized === "") return 0;
    if (!/^\d+(\.\d{0,2})?$/.test(normalized)) return null;
    return Math.round(Number(normalized) * 100);
  })();
  const ready =
    /^[a-z0-9][a-z0-9-]{1,30}$/.test(code) && name.trim().length >= 2 && priceMinor !== null &&
    /^\d+$/.test(trial) && (beds === "" || /^\d+$/.test(beds));

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: name.trim(),
        bed_limit: beds === "" ? null : Number(beds),
        price_minor: priceMinor ?? 0,
        trial_days: Number(trial),
        notes: notes.trim() || null,
      };
      if (plan) await api.platformUpdatePlan(plan.code, body);
      else await api.platformCreatePlan({ code, ...body });
      onSaved();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AdminModal title={plan ? plan.name : t("platform.newPlan")} onClose={onClose} wide>
      <ErrorBanner message={error} />
      <div className="form-grid-2">
        <Field label={t("platform.plan.code")}>
          <input
            style={inputStyle}
            className="tabular"
            value={code}
            disabled={plan !== null}
            autoFocus={plan === null}
            placeholder="fundador"
            onChange={(e) => setCode(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
          />
          <span className="dose-hint">{t("platform.plan.codeHint")}</span>
        </Field>
        <Field label={t("platform.plan.name")}>
          <input style={inputStyle} value={name} placeholder="Fundador" onChange={(e) => setName(e.target.value)} />
        </Field>
      </div>
      <div className="form-grid-3">
        <Field label={t("platform.plan.beds")}>
          <input style={inputStyle} className="tabular" inputMode="numeric" value={beds} placeholder="25" onChange={(e) => setBeds(e.target.value.replace(/\D/g, ""))} />
          <span className="dose-hint">{t("platform.plan.bedsHint")}</span>
        </Field>
        <Field label={t("platform.plan.price")}>
          <input style={inputStyle} className="tabular" inputMode="decimal" value={price} placeholder="197,00" onChange={(e) => setPrice(e.target.value)} />
          <span className="dose-hint">{t("platform.plan.priceHint")}</span>
        </Field>
        <Field label={t("platform.plan.trial")}>
          <input style={inputStyle} className="tabular" inputMode="numeric" value={trial} onChange={(e) => setTrial(e.target.value.replace(/\D/g, ""))} />
          <span className="dose-hint">{t("platform.plan.trialHint")}</span>
        </Field>
      </div>
      <Field label={t("platform.plan.notes")}>
        <input style={inputStyle} value={notes} placeholder={t("platform.plan.notesPlaceholder")} onChange={(e) => setNotes(e.target.value)} />
      </Field>
      {plan ? <AdminNote tone="neutral">{t("platform.plan.editHint")}</AdminNote> : null}
      <div className="admin-toolbar">
        <Button disabled={!ready || busy} onClick={() => void save()}>
          {plan ? t("common.save") : t("platform.plan.create")}
        </Button>
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
      </div>
    </AdminModal>
  );
}

/** O fim do plano fundador, num diálogo: para onde vão, e se ele aposenta. */
function MigrateDialog({ source, targets, onClose, onDone }: { source: Plan; targets: Plan[]; onClose: () => void; onDone: (moved: number, to: string) => void }) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [to, setTo] = useState(targets[0]?.code ?? "");
  const [retire, setRetire] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function migrate() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.platformMigratePlan(source.code, to, retire);
      onDone(result.moved, result.target.name);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AdminModal title={t("platform.plan.migrateTitle", { name: source.name })} onClose={onClose}>
      <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>
        {t("platform.plan.migrateHint", { count: source.clinics })}
      </p>
      <ErrorBanner message={error} />
      <Field label={t("platform.plan.migrateTo")}>
        <Combobox
          value={to}
          onChange={setTo}
          options={targets.map((plan) => ({ value: plan.code, label: plan.name, hint: describePlan(plan, t) }))}
        />
      </Field>
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer" }}>
        <input type="checkbox" checked={retire} onChange={(e) => setRetire(e.target.checked)} style={{ minHeight: 0, width: 20, height: 20, marginTop: 2 }} />
        <span>
          <strong style={{ fontSize: 14 }}>{t("platform.plan.migrateRetire", { name: source.name })}</strong>
          <span style={{ display: "block", fontSize: 13, color: "var(--ink-3)" }}>{t("platform.plan.migrateRetireHint")}</span>
        </span>
      </label>
      <div className="admin-toolbar">
        <Button disabled={!to || busy} onClick={() => void migrate()}>
          {t("platform.plan.migrateDo", { count: source.clinics })}
        </Button>
        <Button variant="secondary" onClick={onClose}>{t("common.cancel")}</Button>
      </div>
    </AdminModal>
  );
}
