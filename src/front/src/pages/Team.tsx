import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, asList } from "../api/client";
import type { MembershipRow } from "../api/types";
import { CAN } from "../api/capabilities";
import {
  AdminModal,
  AdminNote,
  CheckRow,
  initials,
  license,
  usePinRetry,
} from "../components/AdminShared";
import { Gate } from "../components/authz";
import { Combobox } from "../components/Combobox";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
} from "../components/ui";
import "../styles/admin.css";

type Role = "vet" | "tech" | "admin";
const ROLES: Role[] = ["vet", "tech", "admin"];

interface InviteForm {
  name: string;
  email: string;
  password: string;
  role: Role;
  license_number: string;
  license_authority: string;
}

const EMPTY_INVITE: InviteForm = {
  name: "",
  email: "",
  password: "",
  role: "tech",
  license_number: "",
  license_authority: "",
};

export function Team() {
  const { t } = useTranslation();
  const { run, dialog, error, busy, describeError } = usePinRetry();

  // `null` é "ainda não sei", e não "não tem ninguém": falha de carga não pode
  // ser lida como clínica sem equipe.
  const [rows, setRows] = useState<MembershipRow[] | null>(null);
  const [rowsError, setRowsError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [invite, setInvite] = useState<InviteForm | null>(null);
  const [editing, setEditing] = useState<MembershipRow | null>(null);
  const [pinFor, setPinFor] = useState<MembershipRow | null>(null);

  const load = useCallback(async () => {
    setRowsError(null);
    try {
      setRows(asList(await api.memberships()));
    } catch (err) {
      setRowsError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const members = useMemo(() => rows ?? [], [rows]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return members;
    return members.filter(
      (row) =>
        row.name.toLowerCase().includes(needle) ||
        row.email.toLowerCase().includes(needle) ||
        (license(row) ?? "").toLowerCase().includes(needle),
    );
  }, [members, search]);

  async function submitInvite(form: InviteForm) {
    await run(async () => {
      await api.createMembership({
        name: form.name,
        email: form.email,
        password: form.password,
        role: form.role,
        license_number: form.license_number || undefined,
        license_authority: form.license_authority || undefined,
      });
      setInvite(null);
      await load();
    });
  }

  async function submitEdit(row: MembershipRow, body: Record<string, unknown>) {
    await run(async () => {
      await api.updateMembership(row.id, body);
      setEditing(null);
      await load();
    });
  }

  return (
    <>
      {error ? <ErrorState message={error} /> : null}

      <Section
        title={t("team.title")}
        hint={t("team.subtitle")}
        actions={
          <Gate can={CAN.teamManage}>
            <Button onClick={() => setInvite(EMPTY_INVITE)}>{t("team.invite")}</Button>
          </Gate>
        }
      >
        {rowsError ? <ErrorState message={rowsError} onRetry={() => void load()} /> : null}
        {!rowsError && rows === null ? <Skeleton rows={4} /> : null}

        {!rowsError && rows !== null ? (
          <Card style={{ display: "grid", gap: 14 }}>
            <div className="admin-toolbar">
              <input
                type="search"
                style={inputStyle}
                placeholder={t("team.search")}
                aria-label={t("team.search")}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <span className="admin-badge">{t("team.count", { total: visible.length })}</span>
            </div>

            {visible.length === 0 ? (
              <EmptyState
                title={members.length === 0 ? t("team.emptyAll") : t("team.empty")}
                hint={members.length === 0 ? t("team.emptyHint") : t("team.emptySearchHint")}
              />
            ) : (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>{t("team.col.person")}</th>
                      <th>{t("team.col.role")}</th>
                      <th>{t("team.col.license")}</th>
                      <th>{t("team.col.pin")}</th>
                      <th>{t("team.col.status")}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((row) => (
                      <tr key={row.id}>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                            <span className="admin-avatar" aria-hidden="true">
                              {initials(row.name)}
                            </span>
                            <span>
                              <span style={{ display: "block", fontWeight: 600 }}>
                                {row.name || "–"}
                              </span>
                              <span
                                className="admin-cell-muted"
                                style={{ display: "block", fontSize: 12.5 }}
                              >
                                {row.email}
                              </span>
                            </span>
                          </div>
                        </td>
                        <td>{t(`team.role.${row.role}`)}</td>
                        {/* Veterinário sem registro não pode responder por
                            turno nenhum: é falha de conformidade, não célula
                            vazia. Nos outros papéis a ausência é o esperado. */}
                        <td className={license(row) ? "" : "admin-cell-muted"}>
                          {license(row) ??
                            (row.role === "vet" ? (
                              <span className="admin-badge admin-badge-warn">
                                {t("team.licenseMissing")}
                              </span>
                            ) : (
                              "–"
                            ))}
                        </td>
                        <td>
                          <span
                            className={row.has_pin ? "admin-badge admin-badge-on" : "admin-badge"}
                          >
                            {row.has_pin ? t("team.pinSet") : t("team.pinMissing")}
                          </span>
                        </td>
                        <td>
                          <span
                            className={
                              row.is_active
                                ? "admin-badge admin-badge-on"
                                : "admin-badge admin-badge-off"
                            }
                          >
                            {row.is_active ? t("team.active") : t("team.inactive")}
                          </span>
                        </td>
                        <td>
                          <Gate can={CAN.teamManage}>
                            <div
                              style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}
                            >
                              <Button variant="secondary" onClick={() => setPinFor(row)}>
                                {t("team.setPin")}
                              </Button>
                              <Button variant="secondary" onClick={() => setEditing(row)}>
                                {t("team.edit")}
                              </Button>
                            </div>
                          </Gate>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        ) : null}

        {/* O que cada papel pode fazer, em uma frase por papel.
         *
         *  Aqui havia uma TABELA cravada no arquivo dizendo que o administrador
         *  executa tarefa clínica e que o veterinário não tem nenhum limite. O
         *  servidor diz o contrário nos dois casos (`_ADMIN` não tem
         *  `task.execute`; o veterinário não configura a clínica, não gerencia
         *  equipe nem preços), e um teste do back garante isso. Uma tela de
         *  permissões que mente é pior do que tela nenhuma: o administrador
         *  escolhe o papel errado achando que está certo. */}
        <Card style={{ display: "grid", gap: 12 }}>
          <span className="admin-section-label">{t("team.abilities")}</span>
          <div
            className="form-grid-2"
            style={{ gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}
          >
            {ROLES.map((role) => (
              <div key={role} style={{ display: "grid", gap: 4, alignContent: "start" }}>
                <strong style={{ fontSize: 14 }}>{t(`team.role.${role}`)}</strong>
                <span style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
                  {t(`team.roleSummary.${role}`)}
                </span>
              </div>
            ))}
          </div>
          <p className="admin-footnote">{t("team.abilitiesSource")}</p>
        </Card>

        <p className="admin-footnote">{t("team.footer")}</p>
      </Section>


      {invite ? (
        <InviteDialog
          form={invite}
          busy={busy}
          onChange={setInvite}
          onClose={() => setInvite(null)}
          onSubmit={() => void submitInvite(invite)}
        />
      ) : null}

      {editing ? (
        <EditDialog
          row={editing}
          busy={busy}
          onClose={() => setEditing(null)}
          onSubmit={(body) => void submitEdit(editing, body)}
        />
      ) : null}

      {pinFor ? (
        <SetPinDialog
          row={pinFor}
          onClose={() => setPinFor(null)}
          onSaved={() => {
            setPinFor(null);
            void load();
          }}
          run={run}
        />
      ) : null}

      {dialog}
    </>
  );
}

function InviteDialog({
  form,
  busy,
  onChange,
  onClose,
  onSubmit,
}: {
  form: InviteForm;
  busy: boolean;
  onChange: (form: InviteForm) => void;
  onClose: () => void;
  onSubmit: () => void;
}) {
  const { t } = useTranslation();
  const valid =
    form.name.trim() &&
    /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email) &&
    form.password.length >= 8;

  return (
    <AdminModal title={t("team.invite")} onClose={onClose} wide>
      <Field label={t("team.form.name")}>
        <input
          style={inputStyle}
          value={form.name}
          onChange={(event) => onChange({ ...form, name: event.target.value })}
        />
      </Field>
      <Field label={t("team.form.email")}>
        <input
          style={inputStyle}
          type="email"
          value={form.email}
          onChange={(event) => onChange({ ...form, email: event.target.value })}
        />
      </Field>
      <Field label={t("team.form.password")}>
        <input
          style={inputStyle}
          type="password"
          value={form.password}
          onChange={(event) => onChange({ ...form, password: event.target.value })}
        />
        <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>{t("team.form.passwordHint")}</span>
      </Field>
      <Field label={t("team.form.role")}>
        {/* Papel é enum curto (3 opções), mas a regra da tela é: todo select
            vira combobox, sem exceção pra lista pequena. */}
        <Combobox
          value={form.role}
          onChange={(value) => onChange({ ...form, role: value as Role })}
          options={ROLES.map((role) => ({
            value: role,
            label: t(`team.role.${role}`),
            hint: t(`team.roleSummary.${role}`),
          }))}
        />
      </Field>
      <div className="form-grid-2">
        <Field label={t("team.form.licenseAuthority")}>
          <input
            style={inputStyle}
            placeholder="CRMV-SP"
            value={form.license_authority}
            onChange={(event) => onChange({ ...form, license_authority: event.target.value })}
          />
        </Field>
        <Field label={t("team.form.licenseNumber")}>
          <input
            style={inputStyle}
            placeholder="12345"
            value={form.license_number}
            onChange={(event) => onChange({ ...form, license_number: event.target.value })}
          />
        </Field>
      </div>
      <p className="admin-footnote">{t("team.form.licenseHint")}</p>
      <div className="admin-toolbar">
        <Button onClick={onSubmit} disabled={!valid || busy}>
          {t("team.form.create")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
    </AdminModal>
  );
}

function EditDialog({
  row,
  busy,
  onClose,
  onSubmit,
}: {
  row: MembershipRow;
  busy: boolean;
  onClose: () => void;
  onSubmit: (body: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();
  const [role, setRole] = useState<Role>(row.role);
  const [authority, setAuthority] = useState(row.license_authority ?? "");
  const [number, setNumber] = useState(row.license_number ?? "");
  const [active, setActive] = useState(row.is_active);

  return (
    <AdminModal title={row.name || t("team.edit")} onClose={onClose} wide>
      <Field label={t("team.form.role")}>
        <Combobox
          value={role}
          onChange={(value) => setRole(value as Role)}
          options={ROLES.map((option) => ({
            value: option,
            label: t(`team.role.${option}`),
            hint: t(`team.roleSummary.${option}`),
          }))}
        />
        <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
          {t("team.form.roleHint", { role: t(`team.role.${role}`) })}
        </span>
        {/* A frase do papel escolhido aparece no ato da troca: é aqui que o
            administrador decide o que a pessoa vai poder fazer. */}
        <span style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
          {t(`team.roleSummary.${role}`)}
        </span>
      </Field>
      <div className="form-grid-2">
        <Field label={t("team.form.licenseAuthority")}>
          <input
            style={inputStyle}
            value={authority}
            onChange={(event) => setAuthority(event.target.value)}
          />
        </Field>
        <Field label={t("team.form.licenseNumber")}>
          <input
            style={inputStyle}
            value={number}
            onChange={(event) => setNumber(event.target.value)}
          />
        </Field>
      </div>
      <CheckRow
        checked={active}
        onChange={setActive}
        label={t("team.form.active")}
        hint={t("team.form.activeHint")}
      />
      <div className="admin-toolbar">
        <Button
          disabled={busy}
          onClick={() =>
            onSubmit({
              role,
              license_number: number || null,
              license_authority: authority || null,
              is_active: active,
            })
          }
        >
          {t("team.form.save")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
    </AdminModal>
  );
}

function SetPinDialog({
  row,
  onClose,
  onSaved,
  run,
}: {
  row: MembershipRow;
  onClose: () => void;
  onSaved: () => void;
  run: (action: () => Promise<void>) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [pin, setPin] = useState("");
  const [duplicate, setDuplicate] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setDuplicate(false);
    await run(async () => {
      try {
        await api.setMembershipPin(row.id, pin);
        onSaved();
      } catch (err) {
        if (err instanceof ApiError && err.code === "pin_duplicate") setDuplicate(true);
        throw err;
      }
    });
    setBusy(false);
  }

  return (
    <AdminModal title={t("team.setPinFor", { name: row.name })} onClose={onClose}>
      <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-2)" }}>{t("team.pinExplain")}</p>
      <input
        style={{ ...inputStyle, fontSize: 22, letterSpacing: "0.4em", textAlign: "center" }}
        inputMode="numeric"
        autoComplete="off"
        maxLength={4}
        aria-label={t("team.setPin")}
        value={pin}
        onChange={(event) => setPin(event.target.value.replace(/\D/g, "").slice(0, 4))}
      />
      {duplicate ? <AdminNote tone="danger">{t("team.pinDuplicate")}</AdminNote> : null}
      <div className="admin-toolbar">
        <Button disabled={pin.length !== 4 || busy} onClick={() => void submit()}>
          {t("team.form.save")}
        </Button>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
      <p className="admin-footnote">{t("team.pinFooter")}</p>
    </AdminModal>
  );
}
