import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ApiError, api, asList } from "../api/client";
import type {
  ClinicProfile,
  ClinicSettings,
  Kennel,
  MembershipRow,
  Owner,
  PatientSearchHit,
} from "../api/types";
import { Combobox } from "../components/Combobox";
import { PatientsQuickCreate } from "../components/PatientsQuickCreate";
import { PinDialog } from "../components/PinDialog";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import "../styles/patients.css";

type Consent = "consent_recorded" | "emergency_no_consent";

const CONSENTS: Consent[] = ["consent_recorded", "emergency_no_consent"];

/** Cerimônias default da clínica (spec §2): nascem da admissão, sem ninguém pedir. */
interface Ceremony {
  key: string;
  anchor: string | null;
}

const FALLBACK_CEREMONIES: Ceremony[] = [
  { key: "owner_contact", anchor: "16:00" },
  { key: "daily_progress_note", anchor: "08:00" },
];

function ceremoniesOf(clinic: ClinicSettings | null): Ceremony[] {
  const templates = clinic?.default_prescriptions ?? [];
  if (templates.length === 0) return FALLBACK_CEREMONIES;
  return templates.map((template) => {
    const nameKey = typeof template.name_key === "string" ? template.name_key : "";
    return {
      key: nameKey.startsWith("ceremony.") ? nameKey.slice("ceremony.".length) : nameKey,
      anchor: typeof template.anchor === "string" ? template.anchor : null,
    };
  });
}

/** A API de vínculos devolve o nome do profissional; o campo mudou de nome no
 *  contrato (name → user_name), então lemos os dois sem quebrar a tela. */
function personName(row: MembershipRow): string {
  const raw = row as unknown as Record<string, unknown>;
  for (const field of ["user_name", "name", "user_email", "email"]) {
    const value = raw[field];
    if (typeof value === "string" && value) return value;
  }
  return row.id;
}

export function Admission() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const describeError = useApiErrorMessage();

  const [hits, setHits] = useState<PatientSearchHit[]>([]);
  const [picked, setPicked] = useState<PatientSearchHit | null>(null);
  const [kennels, setKennels] = useState<Kennel[]>([]);
  const [owners, setOwners] = useState<Owner[]>([]);
  const [vets, setVets] = useState<MembershipRow[]>([]);
  const [clinic, setClinic] = useState<ClinicSettings | null>(null);
  const [profile, setProfile] = useState<ClinicProfile | null>(null);

  const [patientId, setPatientId] = useState("");
  const [kennelId, setKennelId] = useState("");
  const [vetId, setVetId] = useState("");
  const [consent, setConsent] = useState<Consent>("consent_recorded");
  const [reason, setReason] = useState("");

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [askPin, setAskPin] = useState(false);
  const [quickCreate, setQuickCreate] = useState(false);
  // O nome digitado na busca vira o nome do cadastro: ninguém redigita.
  const [draftName, setDraftName] = useState("");
  const [created, setCreated] = useState<{ id: string; warning: string | null } | null>(null);

  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        // A base de pacientes NÃO é baixada: quem escolhe é a busca no servidor
        // (por nome, microchip ou documento do responsável).
        const [kennelPage, ownerPage, membershipPage] = await Promise.all([
          api.kennels(),
          api.owners(),
          api.memberships(),
        ]);
        if (!alive) return;
        setKennels(asList(kennelPage).filter((kennel) => kennel.is_active));
        setOwners(asList(ownerPage));
        const activeVets = asList(membershipPage).filter(
          (row) => row.role === "vet" && row.is_active,
        );
        setVets(activeVets);
        // Clínica de um vet só: já vem escolhido, ninguém abre o select à toa.
        if (activeVets.length === 1) setVetId(activeVets[0].id);
        setError(null);
      } catch (err) {
        if (alive) setError(describeError(err));
      }
    }
    void load();
    return () => {
      alive = false;
    };
  }, [describeError]);

  useEffect(() => {
    let alive = true;
    // A ocupação é contexto, não requisito: se a clínica não abrir, a tela segue.
    api
      .clinic()
      .then((settings) => {
        if (alive) setClinic(settings);
      })
      .catch(() => undefined);
    // O perfil diz quais identificadores esta clínica usa: microchip na
    // veterinária, CPF/CNS na saúde humana. A tela é a mesma.
    api
      .clinicProfile()
      .then((value) => {
        if (alive) setProfile(value);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // Veio de "Internar" na lista de pacientes: já chega escolhido, sem rebuscar.
  useEffect(() => {
    const preselected = params.get("paciente");
    if (!preselected) return;
    let alive = true;
    void api
      .patient(preselected)
      .then((fresh) => {
        if (!alive) return;
        const hit: PatientSearchHit = {
          id: fresh.id,
          name: fresh.name,
          species: fresh.species,
          breed: fresh.breed,
          owner_id: fresh.owner_id,
          owner_name: "",
          identifiers: [],
          active_hospitalization_id: null,
        };
        setHits((current) => [hit, ...current.filter((item) => item.id !== hit.id)]);
        setPicked(hit);
        setPatientId(hit.id);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [params]);

  // O escolhido entra por ref, não por dependência: se `searchPatients` mudasse
  // de identidade a cada escolha, o efeito do combobox redispararia sozinho.
  // Foi assim que a tela entrou em laço de requisições antes.
  const pickedRef = useRef<PatientSearchHit | null>(null);
  pickedRef.current = picked;

  /** Busca do servidor, chamada pelo combobox com debounce. */
  const searchPatients = useCallback(
    (term: string) => {
      if (term.length < 2) {
        const kept = pickedRef.current;
        setHits((current) => (kept ? current.filter((hit) => hit.id === kept.id) : []));
        return;
      }
      void api
        .searchPatients(term)
        .then(setHits)
        .catch((err) => setError(describeError(err)));
    },
    [describeError],
  );

  const ownerNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const owner of owners) map.set(owner.id, owner.name);
    return map;
  }, [owners]);

  const patient = picked;

  /** O que dá para digitar na busca sai do perfil da clínica, não de um texto
   *  fixo: numa clínica veterinária diz "microchip", numa de saúde humana diz
   *  "CPF, Cartão SUS". */
  const searchPlaceholder = useMemo(() => {
    const kinds = (profile?.patient_identifier_kinds ?? []).map((kind) => t(kind.label_key));
    if (kinds.length === 0) return t("patients.admission.searchHint");
    return t("patients.admission.searchHintWith", { kinds: kinds.join(", ") });
  }, [profile, t]);

  const patientOptions = useMemo(
    () =>
      hits.map((hit) => ({
        value: hit.id,
        label: hit.name,
        // Quem confere na recepção precisa desempatar homônimos sem abrir a ficha.
        hint: [hit.species, hit.breed, hit.owner_name, hit.identifiers[0]?.value]
          .filter(Boolean)
          .join(" · "),
        keywords: hit.identifiers.map((identifier) => identifier.value).join(" "),
      })),
    [hits],
  );

  const ready =
    patientId !== "" &&
    !picked?.active_hospitalization_id &&
    vetId !== "" &&
    (consent === "consent_recorded" || reason.trim() !== "");

  const submit = useCallback(async () => {
    if (!patientId || !vetId) return;
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      patient_id: patientId,
      vet_membership_id: vetId,
      consent_status: consent,
    };
    if (kennelId) body.kennel_id = kennelId;
    if (consent === "emergency_no_consent") body.consent_reason = reason.trim();

    try {
      const result = await api.createHospitalization(body);
      // bed_limit_exceeded é AVISO: a internação existe, o limite de leitos é suave.
      if (result.warning) {
        setCreated({ id: result.hospitalization.id, warning: result.warning });
        return;
      }
      navigate(`/internacao/${result.hospitalization.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [patientId, vetId, kennelId, consent, reason, navigate, describeError]);

  useEffect(() => {
    if (!created) return;
    const timer = setTimeout(() => navigate(`/internacao/${created.id}`), 6000);
    return () => clearTimeout(timer);
  }, [created, navigate]);

  if (created) {
    return (
      <div style={{ display: "grid", gap: 16, maxWidth: 640 }}>
        <h1 style={{ fontSize: 22 }}>{t("patients.admission.created")}</h1>
        <div className="patients-warn" role="status">
          <strong>{t("patients.admission.bedLimit")}</strong>
          <span style={{ fontSize: 13 }}>{t("patients.admission.redirecting")}</span>
        </div>
        <div className="patients-actions">
          <Button onClick={() => navigate(`/internacao/${created.id}`)}>
            {t("patients.admission.goToSheet")}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="patients-split">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        style={{ display: "grid", gap: 14, alignContent: "start" }}
      >
        <header>
          <h1 style={{ fontSize: 24 }}>{t("patients.admission.title")}</h1>
          <p style={{ margin: "2px 0 0", fontSize: 13.5, color: "var(--ink-2)" }}>
            {t("patients.admission.subtitle")}
          </p>
        </header>

        <ErrorBanner message={error} />

        <Card style={{ display: "grid", gap: 14 }}>
          <div className="patients-eyebrow">{t("patients.admission.patientSection")}</div>

          <Field label={t("patients.admission.patient")}>
            <Combobox
              value={patientId}
              onChange={(value) => {
                setPatientId(value);
                setPicked(hits.find((hit) => hit.id === value) ?? null);
              }}
              options={patientOptions}
              onSearch={searchPatients}
              placeholder={searchPlaceholder}
              onCreate={(typed) => {
                setDraftName(typed);
                setQuickCreate(true);
              }}
              createLabel={(typed) =>
                typed
                  ? t("patients.admission.registerNamed", { name: typed })
                  : t("patients.admission.newPatient")
              }
              required
            />
          </Field>

          {/* Internar quem já está internado duplicaria a ficha e a conta. */}
          {picked?.active_hospitalization_id ? (
            <div className="patients-warn">
              <span>{t("patients.admission.alreadyAdmitted", { name: picked.name })}</span>
              <div className="patients-actions">
                <Button
                  onClick={() => navigate(`/internacao/${picked.active_hospitalization_id}`)}
                >
                  {t("patients.admission.openSheet")}
                </Button>
              </div>
            </div>
          ) : null}

          <div className="form-grid-2">
            <Field label={t("patients.admission.kennel")}>
              <Combobox
                value={kennelId}
                onChange={setKennelId}
                emptyLabel={t("patients.admission.noKennel")}
                options={kennels.map((kennel) => ({
                  value: kennel.id,
                  label: kennel.name,
                  hint: kennel.area ?? undefined,
                }))}
              />
            </Field>

            <Field label={t("patients.admission.vet")}>
              <Combobox
                value={vetId}
                onChange={setVetId}
                placeholder={t("patients.admission.chooseVet")}
                options={vets.map((vet) => ({
                  value: vet.id,
                  label: personName(vet),
                  hint: vet.license_number ?? undefined,
                }))}
                required
              />
            </Field>
          </div>

          <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
            {t("patients.admission.kennelHint")}
          </span>

          {vets.length === 0 ? (
            <div className="patients-warn">{t("patients.admission.noVets")}</div>
          ) : null}
        </Card>

        <Card style={{ display: "grid", gap: 14 }}>
          <div className="patients-eyebrow">{t("patients.admission.consent")}</div>
          <div className="chip-group">
            {CONSENTS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={consent === option}
                onClick={() => setConsent(option)}
                className={consent === option ? "chip chip-stacked chip-on" : "chip chip-stacked"}
              >
                <span style={{ fontWeight: 600 }}>
                  {t(`patients.admission.consent.${option}`)}
                </span>
                <span className="chip-hint">
                  {t(`patients.admission.consent.${option}Hint`)}
                </span>
              </button>
            ))}
          </div>

          {consent === "emergency_no_consent" ? (
            <Field label={t("patients.admission.consentReason")}>
              <textarea
                style={{ ...inputStyle, minHeight: 88, resize: "vertical" }}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                placeholder={t("patients.admission.consentReasonPlaceholder")}
                required
              />
            </Field>
          ) : null}
        </Card>

        <div className="patients-actions">
          <Button type="submit" disabled={busy || !ready}>
            {patient
              ? t("patients.admission.submitNamed", { name: patient.name })
              : t("patients.admission.submit")}
          </Button>
          <Button variant="secondary" onClick={() => navigate("/pacientes")}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>

      <aside className="patients-side">
        <Card style={{ display: "grid", gap: 14 }}>
          <div>
            <div className="patients-eyebrow">{t("patients.admission.side.title")}</div>
            <p style={{ margin: "3px 0 0", fontSize: 13, color: "var(--ink-3)" }}>
              {t("patients.admission.side.hint")}
            </p>
          </div>

          <div style={{ display: "grid", gap: 8 }}>
            {ceremoniesOf(clinic).map((ceremony) => (
              <div
                key={ceremony.key}
                style={{
                  border: "1px solid var(--line)",
                  borderRadius: "var(--radius)",
                  padding: "13px 15px",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 14.5 }}>
                  {t(`patients.admission.ceremony.${ceremony.key}`, {
                    defaultValue: ceremony.key,
                  })}
                </div>
                <div style={{ fontSize: 13, color: "var(--ink-3)" }}>
                  {ceremony.anchor
                    ? t(`patients.admission.ceremony.${ceremony.key}Hint`, {
                        time: ceremony.anchor,
                        defaultValue: t("patients.admission.ceremony.daily", {
                          time: ceremony.anchor,
                        }),
                      })
                    : t("patients.admission.ceremony.everyDay")}
                </div>
              </div>
            ))}
          </div>

          {clinic ? (
            <div style={{ display: "grid", gap: 8, borderTop: "1px solid var(--line)", paddingTop: 14 }}>
              <div className="patients-eyebrow" style={{ color: "var(--ink-3)" }}>
                {t("patients.admission.occupancy")}
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <span
                  className="tabular"
                  style={{
                    fontFamily: "'Bricolage Grotesque', system-ui",
                    fontWeight: 800,
                    fontSize: 30,
                    lineHeight: 1,
                  }}
                >
                  {clinic.active_hospitalizations}
                  {clinic.bed_limit ? (
                    <span style={{ fontSize: 19, color: "var(--ink-3)" }}>
                      /{clinic.bed_limit}
                    </span>
                  ) : null}
                </span>
                <span style={{ fontSize: 13, color: "var(--ink-3)" }}>
                  {clinic.plan_tier
                    ? t("patients.admission.bedsPlan", { plan: clinic.plan_tier })
                    : t("patients.admission.beds")}
                </span>
              </div>
              {clinic.bed_limit ? (
                <div className="patients-meter">
                  <div
                    style={{
                      width: `${Math.min(100, Math.round((clinic.active_hospitalizations / clinic.bed_limit) * 100))}%`,
                    }}
                  />
                </div>
              ) : null}
              <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>
                {t("patients.admission.softLimit")}
              </p>
            </div>
          ) : (
            <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>
              {t("patients.admission.softLimit")}
            </p>
          )}

          <div className="patients-note">{t("patients.admission.footer")}</div>
        </Card>
      </aside>

      {askPin ? (
        <PinDialog
          context={patient?.name}
          onDone={() => {
            setAskPin(false);
            void submit();
          }}
          onCancel={() => setAskPin(false)}
        />
      ) : null}

      {quickCreate ? (
        <PatientsQuickCreate
          owners={owners}
          profile={profile}
          initialName={draftName}
          onClose={() => {
            setQuickCreate(false);
            setDraftName("");
          }}
          onCreated={(fresh) => {
            setQuickCreate(false);
            const hit: PatientSearchHit = {
              id: fresh.id,
              name: fresh.name,
              species: fresh.species,
              breed: fresh.breed,
              owner_id: fresh.owner_id,
              owner_name: ownerNames.get(fresh.owner_id) ?? "",
              identifiers: [],
              active_hospitalization_id: null,
            };
            setHits((current) => [hit, ...current.filter((item) => item.id !== hit.id)]);
            setPicked(hit);
            setPatientId(fresh.id);
            void api
              .owners()
              .then((page) => setOwners(asList(page)))
              .catch(() => undefined);
          }}
        />
      ) : null}
    </div>
  );
}
