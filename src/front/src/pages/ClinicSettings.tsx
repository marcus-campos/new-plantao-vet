import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { ClinicProfile, ClinicSettings as ClinicSettingsData } from "../api/types";
import { AdminModal, AdminNote, CheckRow, usePinRetry } from "../components/AdminShared";
import { Combobox } from "../components/Combobox";
import {
  Badge,
  Button,
  Card,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
} from "../components/ui";
import "../styles/admin.css";

/** Frequências que a clínica costuma ancorar. A âncora é chaveada por MINUTOS. */
const PRESET_FREQUENCIES = [1440, 720, 480, 360, 240, 180, 120, 60];

const LOCALES = ["pt-BR", "en"];
const CURRENCIES = ["BRL", "USD", "EUR"];
/** Só entra em cena quando o navegador não sabe listar os fusos da IANA. */
const FALLBACK_TIMEZONES = [
  "America/Sao_Paulo",
  "America/Manaus",
  "America/Fortaleza",
  "America/Bahia",
  "America/New_York",
  "Europe/Lisbon",
  "UTC",
];

/** Tolerâncias padrão do aprazamento (`SchedulingService`, back). Não são
 *  editáveis: a API não tem campo nenhum para elas.
 *
 *  Estavam desenhadas como CAMPOS, dentro do cartão que tem o botão "Salvar".
 *  A clínica olhava, achava que estava configurando e salvava sem efeito
 *  nenhum. Configuração que não configura é pior do que ausente: agora são o
 *  que sempre foram, uma nota do que o sistema faz. */
/** As três janelas, e o campo do rascunho onde cada uma vive.
 *
 *  Eram três constantes cravadas, exibidas como etiquetas ao lado da legenda
 *  "só leitura: nada aqui é configurável pela clínica hoje". Os valores ISMP
 *  (30/60/120) continuam sendo o default: agora são o ponto de partida. */
const TOLERANCE_FIELDS = [
  { key: "critical", draft: "toleranceCritical" },
  { key: "normal", draft: "toleranceNormal" },
  { key: "daily", draft: "toleranceDaily" },
] as const;

/** Piso e teto da janela, em minutos. Os mesmos do backend
 *  (`ClinicUpdate`): a interface avisa antes, a API recusa de qualquer jeito. */
const TOLERANCE_MIN = 5;
const TOLERANCE_MAX = 1440;

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Fusos que o navegador conhece: a lista da IANA inteira, quando disponível.
 *
 *  O campo era texto livre e o fuso é a régua de TODO horário do produto:
 *  "Sao Paulo" no lugar de "America/Sao_Paulo" é aceito pela digitação, e é a
 *  próxima prescrição que quebra. */
function timezoneOptions(current: string): string[] {
  const supported = (
    Intl as unknown as { supportedValuesOf?: (key: string) => string[] }
  ).supportedValuesOf;
  const list = typeof supported === "function" ? supported("timeZone") : FALLBACK_TIMEZONES;
  // O fuso já salvo entra na lista nem que o navegador não o conheça: sumir
  // com o valor atual do campo faria a tela parecer vazia.
  return list.includes(current) ? list : [current, ...list];
}

interface Draft {
  name: string;
  /** Timbre do prontuário. A tela do prontuário lia estes três campos por
   *  cast e eles nunca existiram na API: o documento entregue ao tutor saía
   *  sem endereço, telefone nem CNPJ. */
  address: string;
  phone: string;
  taxId: string;
  compliance_profile: string;
  locale: string;
  currency: string;
  unit_system: "metric" | "imperial";
  timezone: string;
  anchors: Record<string, string[]>;
  /** Minutos, como string: o campo é de texto e "" é um estado legítimo
   *  enquanto se digita. Vira número só na hora de montar o corpo. */
  toleranceCritical: string;
  toleranceNormal: string;
  toleranceDaily: string;
  /** As cerimônias da admissão. A clínica liga, desliga e muda o horário; o
   *  que cada uma É continua sendo conteúdo nosso (`name_key`), traduzido no
   *  locale dela. Um editor livre aqui viraria um construtor de prescrição
   *  paralelo ao que já existe na ficha do paciente. */
  ceremonies: Ceremony[];
}

interface Ceremony {
  /** `name_key` do catálogo, ou o nome digitado por uma clínica antiga. */
  key: string | null;
  name: string | null;
  enabled: boolean;
  /** "08:00". Vazio = sem âncora, a cerimônia cai no offset do início. */
  anchor: string;
  /** O resto do template, preservado intacto: categoria, tipo, frequência e
   *  criticidade não são editáveis aqui e não podem se perder no caminho. */
  raw: Record<string, unknown>;
}

function toDraft(clinic: ClinicSettingsData): Draft {
  return {
    name: clinic.name,
    address: clinic.address ?? "",
    phone: clinic.phone ?? "",
    taxId: clinic.tax_id ?? "",
    compliance_profile: clinic.compliance_profile,
    locale: clinic.locale,
    currency: clinic.currency,
    unit_system: clinic.unit_system,
    timezone: clinic.timezone,
    anchors: Object.fromEntries(
      Object.entries(clinic.anchors).map(([minutes, times]) => [minutes, [...times]]),
    ),
    toleranceCritical: String(clinic.tolerance_critical_minutes),
    toleranceNormal: String(clinic.tolerance_normal_minutes),
    toleranceDaily: String(clinic.tolerance_daily_minutes),
    ceremonies: toCeremonies(clinic.default_prescriptions),
  };
}

/** Espelho de `DEFAULT_PRESCRIPTIONS` (app/models/clinic.py): as cerimônias
 *  que o produto conhece.
 *
 *  Existe porque o modelo guarda só as LIGADAS. Sem este catálogo, desligar
 *  uma cerimônia seria uma porta de mão única: ela sumiria da lista e não
 *  haveria de onde trazê-la de volta. */
const CEREMONY_CATALOG: Record<string, unknown>[] = [
  {
    name_key: "ceremony.owner_contact",
    category: "care",
    kind: "recurring",
    frequency_minutes: 1440,
    criticality: "normal",
    anchor: "16:00",
  },
  {
    name_key: "ceremony.daily_progress_note",
    category: "care",
    kind: "recurring",
    frequency_minutes: 1440,
    criticality: "normal",
    anchor: "08:00",
  },
];

/** O catálogo unido ao que a clínica gravou: primeiro as conhecidas, depois
 *  qualquer cerimônia que a clínica tenha por conta própria. */
function toCeremonies(saved: Record<string, unknown>[]): Ceremony[] {
  const identidade = (item: Record<string, unknown>) => {
    const { key, name } = templateKey(item);
    return key ?? name ?? JSON.stringify(item);
  };
  const gravadas = new Map(saved.map((item) => [identidade(item), item]));
  const lista: Ceremony[] = [];
  const vistos = new Set<string>();
  for (const template of [...CEREMONY_CATALOG, ...saved]) {
    const id = identidade(template);
    if (vistos.has(id)) continue;
    vistos.add(id);
    const gravado = gravadas.get(id);
    const fonte = gravado ?? template;
    const { key, name } = templateKey(fonte);
    lista.push({
      key,
      name,
      enabled: gravado !== undefined,
      anchor: typeof fonte.anchor === "string" ? fonte.anchor : "",
      raw: fonte,
    });
  }
  return lista;
}

/** A lista de volta ao formato do modelo: só as ligadas, com o horário
 *  editado e o resto do template intacto. */
function fromCeremonies(list: Ceremony[]): Record<string, unknown>[] {
  return list
    .filter((item) => item.enabled)
    .map((item) => {
      const next = { ...item.raw };
      if (item.anchor) next.anchor = item.anchor;
      else delete next.anchor;
      return next;
    });
}

/** O que identifica uma prescrição padrão no modelo.
 *
 *  O template guarda `name_key`: conteúdo NOSSO, traduzido no locale da
 *  clínica na hora de criar a prescrição. A tela lia `item.name`, que não
 *  existe no template: TODA clínica via "#1" e "#2" no lugar de "Contato com o
 *  tutor" e "Evolução diária". */
function templateKey(item: Record<string, unknown>): { key: string | null; name: string | null } {
  return {
    key: typeof item.name_key === "string" && item.name_key ? item.name_key : null,
    name: typeof item.name === "string" && item.name ? item.name : null,
  };
}

export function ClinicSettings() {
  const { t } = useTranslation();
  const { run, dialog, error, busy, describeError } = usePinRetry();

  const [clinic, setClinic] = useState<ClinicSettingsData | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [saved, setSaved] = useState(false);
  /** Campo recusado pela API (422 `validation_error` traz `field`). */
  const [badField, setBadField] = useState<string | null>(null);
  const [askRotate, setAskRotate] = useState(false);
  const [newStationKey, setNewStationKey] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ClinicProfile[]>([]);
  const [profilesError, setProfilesError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const data = await api.clinic();
      setClinic(data);
      setDraft(toDraft(data));
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let alive = true;
    // A lista vem do servidor: perfil novo aparece aqui sem mexer nesta tela.
    void api
      .clinicProfiles()
      .then((value) => {
        if (!alive) return;
        setProfiles(value);
        setProfilesError(null);
      })
      .catch((err) => {
        // Sem os perfis a caixa de área de atuação fica vazia. Dizer o motivo
        // evita a leitura "esta instalação só tem um perfil".
        if (alive) setProfilesError(describeError(err));
      });
    return () => {
      alive = false;
    };
  }, [describeError]);

  /** Só o que mudou vai no PATCH: não reescrevemos âncoras ao trocar o nome. */
  const changes = useMemo<Record<string, unknown>>(() => {
    if (!clinic || !draft) return {};
    const body: Record<string, unknown> = {};
    if (draft.name !== clinic.name) body.name = draft.name;
    // Vazio vira null: um campo do timbre em branco é "não informado", não uma
    // string vazia impressa no papel entregue ao tutor.
    if (draft.address !== (clinic.address ?? "")) body.address = draft.address || null;
    if (draft.phone !== (clinic.phone ?? "")) body.phone = draft.phone || null;
    if (draft.taxId !== (clinic.tax_id ?? "")) body.tax_id = draft.taxId || null;
    if (draft.compliance_profile !== clinic.compliance_profile) {
      body.compliance_profile = draft.compliance_profile;
    }
    if (draft.locale !== clinic.locale) body.locale = draft.locale;
    if (draft.currency !== clinic.currency) body.currency = draft.currency;
    if (draft.unit_system !== clinic.unit_system) body.unit_system = draft.unit_system;
    if (draft.timezone !== clinic.timezone) body.timezone = draft.timezone;
    if (JSON.stringify(draft.anchors) !== JSON.stringify(clinic.anchors)) {
      body.anchors = draft.anchors;
    }
    // Tolerâncias: só vão no corpo se forem um número válido E diferente do
    // que está gravado. Campo em branco no meio da digitação não pode virar
    // uma janela de zero minutos, que faria toda tarefa nascer atrasada.
    const tolerancias: [keyof Draft, keyof ClinicSettingsData][] = [
      ["toleranceCritical", "tolerance_critical_minutes"],
      ["toleranceNormal", "tolerance_normal_minutes"],
      ["toleranceDaily", "tolerance_daily_minutes"],
    ];
    for (const [campo, coluna] of tolerancias) {
      const valor = Number(draft[campo]);
      if (Number.isInteger(valor) && valor > 0 && valor !== clinic[coluna]) {
        body[coluna] = valor;
      }
    }
    const cerimonias = fromCeremonies(draft.ceremonies);
    if (JSON.stringify(cerimonias) !== JSON.stringify(clinic.default_prescriptions)) {
      body.default_prescriptions = cerimonias;
    }
    return body;
  }, [clinic, draft]);

  /** Fora da faixa a API recusa, e recusar depois de digitar é pior do que
   *  dizer antes. Salvar continua possível: o que está fora simplesmente não
   *  entra no corpo, e o aviso explica por quê. */
  const toleranceOutOfRange = TOLERANCE_FIELDS.some((field) => {
    const valor = Number(draft?.[field.draft] ?? "");
    return (
      !Number.isInteger(valor) || valor < TOLERANCE_MIN || valor > TOLERANCE_MAX
    );
  });

  const dirty = Object.keys(changes).length > 0;

  function patch(next: Partial<Draft>) {
    setSaved(false);
    // Mexer no campo recusado limpa a recusa: manter o aviso depois da
    // correção faz a pessoa achar que salvar de novo não adianta.
    if (next.timezone !== undefined && badField === "timezone") setBadField(null);
    setDraft((current) => (current ? { ...current, ...next } : current));
  }

  /** Uma cerimônia por vez, sem tocar nas outras. */
  function patchCeremony(index: number, next: Partial<Ceremony>) {
    if (!draft) return;
    const lista = draft.ceremonies.map((item, i) => (i === index ? { ...item, ...next } : item));
    patch({ ceremonies: lista });
  }

  function setAnchors(next: Record<string, string[]>) {
    if (badField === "anchors") setBadField(null);
    patch({ anchors: next });
  }

  function addTime(minutes: string, time: string) {
    if (!draft || !HHMM.test(time)) return;
    const current = draft.anchors[minutes] ?? [];
    if (current.includes(time)) return;
    setAnchors({ ...draft.anchors, [minutes]: [...current, time].sort() });
  }

  function removeTime(minutes: string, time: string) {
    if (!draft) return;
    const remaining = (draft.anchors[minutes] ?? []).filter((value) => value !== time);
    const next = { ...draft.anchors };
    // Frequência sem nenhum horário não existe para a API: some da lista.
    if (remaining.length === 0) delete next[minutes];
    else next[minutes] = remaining;
    setAnchors(next);
  }

  async function save() {
    if (!dirty) return;
    await run(async () => {
      try {
        const updated = await api.updateClinic(changes);
        setClinic(updated);
        setDraft(toDraft(updated));
        setForbidden(false);
        setBadField(null);
        setSaved(true);
      } catch (err) {
        if (err instanceof ApiError && err.code === "forbidden") setForbidden(true);
        // 422 com o campo recusado: o erro pertence AO CAMPO. Como banner
        // genérico ("dados inválidos") ninguém descobre que o problema é o
        // fuso digitado, e o fuso é a régua de todo horário do produto.
        if (
          err instanceof ApiError &&
          err.code === "validation_error" &&
          typeof err.params.field === "string"
        ) {
          setBadField(err.params.field);
        }
        throw err;
      }
    });
  }

  async function rotate() {
    setAskRotate(false);
    await run(async () => {
      try {
        const result = await api.rotateStationKey();
        setNewStationKey(result.station_key);
        setCopied(false);
        setForbidden(false);
        setClinic((current) =>
          current ? { ...current, station_key_version: result.station_key_version } : current,
        );
      } catch (err) {
        if (err instanceof ApiError && err.code === "forbidden") setForbidden(true);
        throw err;
      }
    });
  }

  const frequencies = useMemo(
    () =>
      Object.keys(draft?.anchors ?? {})
        .map(Number)
        .filter((minutes) => Number.isFinite(minutes) && minutes > 0)
        .sort((a, b) => b - a),
    [draft],
  );

  const timezones = useMemo(
    () => timezoneOptions(draft?.timezone ?? "UTC"),
    [draft?.timezone],
  );

  function frequencyLabel(minutes: number): string {
    return minutes % 60 === 0
      ? t("settings.everyHours", { hours: minutes / 60 })
      : t("settings.everyMinutes", { minutes });
  }

  if (loadError) return <ErrorState message={loadError} onRetry={() => void load()} />;
  if (!draft || !clinic) return <Skeleton rows={4} height={96} />;

  const saveBar = (
    <div className="admin-toolbar">
      <Button onClick={() => void save()} disabled={!dirty || busy}>
        {t("settings.save")}
      </Button>
      {saved ? <Badge tone="good">{t("settings.saved")}</Badge> : null}
      {dirty ? (
        <span className="admin-footnote">
          {t("settings.unsaved", { n: Object.keys(changes).length })}
        </span>
      ) : null}
    </div>
  );

  return (
    <>
      {error ? <ErrorState message={error} /> : null}
      {forbidden ? <AdminNote tone="danger">{t("settings.forbidden")}</AdminNote> : null}

      <Section title={t("settings.section.clinic")} hint={t("settings.section.clinicHint")}>
        <Card style={{ display: "grid", gap: 14 }}>
          <Field label={t("settings.clinicName")}>
            <input
              style={inputStyle}
              value={draft.name}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </Field>
          <Field label={t("settings.clinic.address")}>
            <input
              style={inputStyle}
              value={draft.address}
              onChange={(event) => patch({ address: event.target.value })}
            />
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
              {t("settings.clinic.letterheadHint")}
            </span>
          </Field>
          <div className="form-grid-2">
            <Field label={t("settings.clinic.phone")}>
              <input
                style={inputStyle}
                value={draft.phone}
                onChange={(event) => patch({ phone: event.target.value })}
              />
            </Field>
            <Field label={t("settings.clinic.taxId")}>
              <input
                style={inputStyle}
                value={draft.taxId}
                onChange={(event) => patch({ taxId: event.target.value })}
              />
            </Field>
          </div>
          {/* Plano e limite são LEITURA aqui. O leito é a unidade de cobrança,
              e o campo ficava editável pelo administrador da clínica, que podia
              subir o próprio limite. Quem vende é quem muda; a API recusa o
              campo com 422 se alguém tentar por fora. */}
          <div className="settings-plan">
            <div>
              <span className="admin-section-label">{t("settings.plan")}</span>
              <div className="settings-plan-name">
                {clinic.plan_name ?? clinic.plan_tier ?? t("settings.planNone")}
                {clinic.bed_limit !== null ? (
                  <span className="settings-plan-beds tabular">
                    {" · "}
                    {t("platform.beds", { count: clinic.bed_limit })}
                  </span>
                ) : null}
              </div>
            </div>
            <div className="settings-plan-occupancy tabular">
              {clinic.bed_limit === null
                ? t("settings.occupancyNoLimit", { active: clinic.active_hospitalizations })
                : t("settings.occupancy", {
                    active: clinic.active_hospitalizations,
                    limit: clinic.bed_limit,
                  })}
            </div>
          </div>
          <p className="admin-footnote">{t("settings.planChange")}</p>
        </Card>
      </Section>

      <Section title={t("settings.section.hours")} hint={t("settings.section.hoursHint")}>
        <Card style={{ display: "grid", gap: 2 }}>
          <span className="admin-section-label">{t("settings.anchors")}</span>
          <p style={{ margin: "2px 0 8px", fontSize: 13, color: "var(--ink-3)" }}>
            {t("settings.anchorsHint")}
          </p>

          {frequencies.length === 0 ? (
            <p style={{ margin: "8px 0", fontSize: 13.5, color: "var(--ink-3)" }}>
              {t("settings.anchorsEmpty")}
            </p>
          ) : null}

          {frequencies.map((minutes) => (
            <AnchorRow
              key={minutes}
              label={frequencyLabel(minutes)}
              times={draft.anchors[String(minutes)] ?? []}
              onAdd={(time) => addTime(String(minutes), time)}
              onRemove={(time) => removeTime(String(minutes), time)}
            />
          ))}

          <div style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 12 }}>
            <NewFrequency
              existing={frequencies}
              onAdd={(minutes, time) => addTime(String(minutes), time)}
            />
          </div>
        </Card>
        {badField === "anchors" ? (
          <AdminNote tone="danger">{t("settings.anchorsRejected")}</AdminNote>
        ) : null}
        <AdminNote>{t("settings.anchorsWarning")}</AdminNote>
      </Section>

      <Section title={t("settings.section.region")} hint={t("settings.section.regionHint")}>
        <Card style={{ display: "grid", gap: 14 }}>
          <Field label={t("settings.complianceProfile")}>
            <Combobox
              value={draft.compliance_profile}
              onChange={(value) => patch({ compliance_profile: value })}
              options={profiles.map((profile) => ({
                value: profile.profile,
                label: t(profile.name_key),
                // O que muda de fato ao trocar, dito na própria opção.
                hint: t("settings.complianceProfileOption", {
                  kinds: profile.patient_identifier_kinds
                    .map((kind) => t(kind.label_key))
                    .join(", "),
                  years: profile.retention_years,
                }),
              }))}
            />
          </Field>
          {profilesError ? <AdminNote tone="danger">{profilesError}</AdminNote> : null}
          <p className="admin-footnote">{t("settings.complianceProfileHint")}</p>

          <div className="form-grid-2">
            <Field label={t("settings.locale")}>
              <Combobox
                value={draft.locale}
                onChange={(value) => patch({ locale: value })}
                options={LOCALES.map((locale) => ({
                  value: locale,
                  label: t(`settings.locale.${locale}`),
                }))}
              />
            </Field>
            <Field label={t("settings.currency")}>
              <Combobox
                value={draft.currency}
                onChange={(value) => patch({ currency: value })}
                options={CURRENCIES.map((currency) => ({ value: currency, label: currency }))}
              />
            </Field>
          </div>
          <div className="form-grid-2">
            <Field label={t("settings.unitSystem")}>
              <Combobox
                value={draft.unit_system}
                onChange={(value) => patch({ unit_system: value as "metric" | "imperial" })}
                options={[
                  { value: "metric", label: t("settings.unitSystem.metric") },
                  { value: "imperial", label: t("settings.unitSystem.imperial") },
                ]}
              />
            </Field>
            <Field label={t("settings.timezone")}>
              <Combobox
                value={draft.timezone}
                onChange={(value) => patch({ timezone: value })}
                options={timezones.map((zone) => ({ value: zone, label: zone }))}
                placeholder={t("settings.timezonePick")}
              />
              {badField === "timezone" ? (
                <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--late)" }}>
                  {t("settings.timezoneRejected")}
                </span>
              ) : null}
            </Field>
          </div>
          <p className="admin-footnote">{t("settings.regionHint")}</p>
        </Card>
      </Section>

      {/* As regras clínicas entram ANTES do "Salvar": elas passaram a ser
          editáveis, e um bloco editável abaixo da barra de salvar seria um
          formulário sem botão. */}
      <Section title={t("settings.section.rules")} hint={t("settings.rulesHint")}>
        <Card style={{ display: "grid", gap: 14 }}>
          <div>
            <span className="admin-section-label">{t("settings.tolerances")}</span>
            <p className="admin-footnote" style={{ margin: "4px 0 0" }}>
              {t("settings.tolerancesHint")}
            </p>
          </div>

          <div className="form-grid-3">
            {TOLERANCE_FIELDS.map((field) => (
              <Field key={field.key} label={t(`settings.tolerance.${field.key}`)}>
                <input
                  style={inputStyle}
                  className="tabular"
                  inputMode="numeric"
                  value={draft[field.draft]}
                  onChange={(event) => patch({ [field.draft]: event.target.value } as Partial<Draft>)}
                />
                <span className="dose-hint">{t(`settings.toleranceHint.${field.key}`)}</span>
              </Field>
            ))}
          </div>

          {/* Uma janela de zero faria toda tarefa nascer atrasada; uma de uma
              semana faria "atrasada" nunca acontecer. Nos dois casos o estado
              deixa de informar, que é o oposto do que ele serve. */}
          {toleranceOutOfRange ? (
            <AdminNote tone="danger">{t("settings.toleranceRange")}</AdminNote>
          ) : (
            <AdminNote tone="neutral">{t("settings.toleranceEffect")}</AdminNote>
          )}
        </Card>

        <Card style={{ display: "grid", gap: 14 }}>
          <div>
            <span className="admin-section-label">{t("settings.defaultPrescriptions")}</span>
            <p className="admin-footnote" style={{ margin: "4px 0 0" }}>
              {t("settings.defaultPrescriptionsHint")}
            </p>
          </div>

          {draft.ceremonies.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-3)" }}>
              {t("settings.defaultPrescriptionsEmpty")}
            </p>
          ) : (
            draft.ceremonies.map((ceremony, index) => (
              <div key={ceremony.key ?? index} className="ceremony-row">
                <CheckRow
                  checked={ceremony.enabled}
                  onChange={(value) => patchCeremony(index, { enabled: value })}
                  label={
                    ceremony.key
                      ? t(ceremony.key, { defaultValue: ceremony.key })
                      : (ceremony.name ??
                        t("settings.defaultPrescriptionUnnamed", { n: index + 1 }))
                  }
                  hint={t("settings.ceremonyDaily")}
                />
                {/* O horário só aparece quando a cerimônia está ligada: um
                    campo de hora ao lado de algo desligado é uma decisão que
                    não muda nada. */}
                {ceremony.enabled ? (
                  <label className="ceremony-time">
                    <span>{t("settings.ceremonyAt")}</span>
                    <input
                      type="time"
                      style={{ ...inputStyle, width: 130 }}
                      className="tabular"
                      value={ceremony.anchor}
                      onChange={(event) => patchCeremony(index, { anchor: event.target.value })}
                    />
                  </label>
                ) : null}
              </div>
            ))
          )}
        </Card>
      </Section>

      {/* O "Salvar" fecha o formulário, depois do último campo editável: é o
          fim do trabalho, não o começo. O que vem abaixo (a chave de estação)
          não passa por ele. */}
      {saveBar}

      <Section title={t("settings.section.security")} hint={t("settings.section.securityHint")}>
        {/* Aparelhos é o caminho agora: cada um com acesso próprio, revogável
            sozinho. A chave única continua abaixo porque há aparelho em campo
            que só conhece ela, mas deixa de ser a primeira coisa que se vê. */}
        <Card style={{ display: "grid", gap: 10 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{t("settings.devicesTitle")}</div>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--ink-3)" }}>
              {t("settings.devicesHint")}
            </p>
          </div>
          <Link to="/gestao/aparelhos" className="settings-link">
            {t("settings.devicesLink")}
          </Link>
        </Card>

        <Card style={{ display: "grid", gap: 14 }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{t("settings.stationKey")}</div>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--ink-3)" }}>
              {t("settings.stationKeyHint")}
            </p>
          </div>

          <div className="admin-toolbar">
            <span className="admin-badge">
              {t("settings.stationKeyVersion", { version: clinic.station_key_version })}
            </span>
            <Button variant="danger" onClick={() => setAskRotate(true)} disabled={busy}>
              {t("settings.rotate")}
            </Button>
          </div>

          <AdminNote tone="danger">{t("settings.rotateWarning")}</AdminNote>

          {newStationKey ? (
            <div style={{ display: "grid", gap: 8 }}>
              <span className="admin-section-label">{t("settings.newKey")}</span>
              <div className="admin-secret">{newStationKey}</div>
              <div className="admin-toolbar">
                <Button
                  variant="secondary"
                  onClick={() => {
                    // A chave em claro sai da API UMA vez. Sem área de
                    // transferência (origem http, permissão negada) o botão não
                    // pode ficar quieto fingindo que copiou: quem fecha a tela
                    // confiando nisso perde a chave para sempre.
                    const clipboard = navigator.clipboard;
                    if (!clipboard) {
                      setCopyError(t("settings.copyFailed"));
                      return;
                    }
                    void clipboard.writeText(newStationKey).then(
                      () => {
                        setCopied(true);
                        setCopyError(null);
                      },
                      () => setCopyError(t("settings.copyFailed")),
                    );
                  }}
                >
                  {copied ? t("settings.copied") : t("settings.copy")}
                </Button>
                <Button variant="secondary" onClick={() => setNewStationKey(null)}>
                  {t("settings.hideKey")}
                </Button>
              </div>
              {copyError ? <AdminNote tone="danger">{copyError}</AdminNote> : null}
              <p className="admin-footnote">{t("settings.newKeyHint")}</p>
            </div>
          ) : null}

          <p className="admin-footnote">{t("settings.pinHint")}</p>
        </Card>
      </Section>

      <p className="admin-footnote">{t("settings.footer")}</p>

      {askRotate ? (
        <AdminModal title={t("settings.rotate")} onClose={() => setAskRotate(false)}>
          <AdminNote tone="danger">{t("settings.rotateWarning")}</AdminNote>
          <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-2)" }}>
            {t("settings.rotateConfirm")}
          </p>
          <div className="admin-toolbar">
            <Button variant="danger" onClick={() => void rotate()} disabled={busy}>
              {t("settings.rotateDo")}
            </Button>
            <Button variant="secondary" onClick={() => setAskRotate(false)}>
              {t("common.cancel")}
            </Button>
          </div>
        </AdminModal>
      ) : null}

      {dialog}
    </>
  );
}

function AnchorRow({
  label,
  times,
  onAdd,
  onRemove,
}: {
  label: string;
  times: string[];
  onAdd: (time: string) => void;
  onRemove: (time: string) => void;
}) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");

  return (
    <div className="anchor-row">
      <div className="anchor-label">{label}</div>
      <div className="anchor-times">
        {times.map((time) => (
          <span key={time} className="time-chip">
            {time}
            <button
              type="button"
              className="time-chip-remove"
              aria-label={t("settings.removeTime", { time })}
              onClick={() => onRemove(time)}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="anchor-add">
        <input
          type="time"
          style={{ ...inputStyle, width: 120 }}
          value={value}
          aria-label={t("settings.addTime")}
          onChange={(event) => setValue(event.target.value)}
        />
        <Button
          variant="secondary"
          disabled={!HHMM.test(value)}
          onClick={() => {
            onAdd(value);
            setValue("");
          }}
        >
          {t("settings.addTime")}
        </Button>
      </div>
    </div>
  );
}

function NewFrequency({
  existing,
  onAdd,
}: {
  existing: number[];
  onAdd: (minutes: number, time: string) => void;
}) {
  const { t } = useTranslation();
  const [minutes, setMinutes] = useState("");
  const [time, setTime] = useState("10:00");

  const available = PRESET_FREQUENCIES.filter((value) => !existing.includes(value));
  const value = Number(minutes);
  const valid = Number.isFinite(value) && value > 0 && HHMM.test(time);

  return (
    <div className="anchor-add">
      {/* Sem "nenhum" aqui: enquanto não escolhe uma frequência, o botão "adicionar" fica desabilitado. */}
      <Combobox
        value={minutes}
        onChange={setMinutes}
        placeholder={t("settings.addFrequency")}
        options={available.map((preset) => ({
          value: String(preset),
          label:
            preset % 60 === 0
              ? t("settings.everyHours", { hours: preset / 60 })
              : t("settings.everyMinutes", { minutes: preset }),
        }))}
      />
      <input
        type="time"
        style={{ ...inputStyle, width: 120 }}
        value={time}
        aria-label={t("settings.addTime")}
        onChange={(event) => setTime(event.target.value)}
      />
      <Button
        variant="secondary"
        disabled={!valid}
        onClick={() => {
          onAdd(value, time);
          setMinutes("");
        }}
      >
        {t("settings.addFrequencyDo")}
      </Button>
    </div>
  );
}
