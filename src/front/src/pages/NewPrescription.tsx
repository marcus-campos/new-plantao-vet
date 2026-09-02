import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useParams } from "react-router-dom";

import { CATEGORIES, CATEGORY_PROFILE } from "../api/categories";
import { ApiError, api } from "../api/client";
import type {
  DosePreview,
  PrescriptionCategory,
  PrescriptionKind,
  PriceListItem,
  SchedulePreview,
} from "../api/types";
import { Combobox } from "../components/Combobox";
import { PinDialog } from "../components/PinDialog";
import {
  Button,
  Card,
  ErrorBanner,
  Field,
  decimal,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";

const FREQUENCIES = [30, 60, 120, 240, 360, 480, 720, 1440];

/** O que se prescreve sugere como é dado. Fluido nasce contínuo; o resto, com horário.
 *  É só um default: infusão contínua de fármaco (CRI) segue possível. */
const KIND_BY_CATEGORY: Record<PrescriptionCategory, PrescriptionKind> = {
  medication: "recurring",
  fluids: "continuous",
  monitoring: "recurring",
  nutrition: "recurring",
  care: "recurring",
  procedure: "recurring",
};

export function NewPrescription() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { moment, money } = useClinic();
  const describeError = useApiErrorMessage();

  const [kind, setKind] = useState<PrescriptionKind>("recurring");
  const [category, setCategory] = useState<PrescriptionCategory>("medication");
  const [name, setName] = useState("");
  const [frequency, setFrequency] = useState(480);
  const [duration, setDuration] = useState("72");
  const [criticality, setCriticality] = useState<"normal" | "critical">("normal");
  const [firstDoseNow, setFirstDoseNow] = useState(false);
  const [rate, setRate] = useState("60");
  const [price, setPrice] = useState("");
  const [catalog, setCatalog] = useState<PriceListItem[]>([]);
  const [catalogItemId, setCatalogItemId] = useState("");
  const [overridePrice, setOverridePrice] = useState(false);
  // Vazio = agora. O vet pode programar o começo para outro dia/hora.
  const [startsAt, setStartsAt] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [askPin, setAskPin] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void api
      .priceList()
      .then((page) => {
        if (alive) setCatalog(page.items.filter((item) => !item.is_daily_rate));
      })
      .catch(() => {
        // Catálogo é conveniência: sem ele a prescrição segue com valor avulso.
      });
    return () => {
      alive = false;
    };
  }, []);

  const perfil = CATEGORY_PROFILE[category];
  const catalogItem = catalog.find((item) => item.id === catalogItemId) ?? null;

  /** A dose, calculada pelo servidor para ESTE paciente.
   *
   *  O sistema tem as duas coisas que faltavam: a concentração da apresentação
   *  e o peso e a espécie do paciente. Perguntar de novo o que ele já sabe é
   *  pedir ao veterinário a conta que a máquina faz certo, e 51% dos erros de
   *  medicação são de dose. */
  /** Quem é o paciente. Prescrever é um ato sobre alguém, e a tela não dizia
   *  sobre quem: a rota é de página inteira e perdia o cabeçalho do paciente
   *  que as abas mantêm na tela o tempo todo. */
  const [patient, setPatient] = useState<{ name: string; kennel: string | null } | null>(null);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    void api
      .hospitalization(id)
      .then((detail) => {
        if (alive) {
          setPatient({
            name: detail.patient?.name ?? "",
            kennel: detail.kennel_name,
          });
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [id]);

  const [dosePerKg, setDosePerKg] = useState("");
  const [dose, setDose] = useState<DosePreview | null>(null);
  const [doseTouched, setDoseTouched] = useState(false);

  useEffect(() => {
    if (!catalogItemId || !id) {
      setDose(null);
      return;
    }
    const timer = setTimeout(() => {
      void api
        .previewDose({
          price_list_item_id: catalogItemId,
          hospitalization_id: id,
          dose_per_kg: doseTouched && dosePerKg !== "" ? decimal(dosePerKg) : null,
        })
        .then((value) => {
          setDose(value);
          // Só pré-preenche o que um veterinário conferiu. Uma regra sem
          // revisão aparece como referência, nunca como resposta.
          if (!doseTouched && value.reviewed && value.dose_per_kg) {
            // "25.0000" é o Decimal do servidor; num campo de dose isso se lê
            // como quatro casas de precisão que a posologia não tem.
            setDosePerKg(String(Number(value.dose_per_kg)));
          }
        })
        .catch(() => {
          // Mantém o último cálculo bom: apagar tudo a cada tecla intermediária
          // ("0," ainda não é número) fazia o bloco piscar e parecer quebrado.
        });
    }, 200);
    return () => clearTimeout(timer);
  }, [catalogItemId, id, dosePerKg, doseTouched]);


  /** O aprazamento vem do SERVIDOR.
   *
   *  Havia aqui uma segunda implementação da regra inteira (âncoras, offset,
   *  supressão da primeira dose) com a tabela de âncoras cravada no código,
   *  enquanto Configurações deixa a clínica editar a dela. A tela prometia
   *  horários que o servidor não ia criar. `POST /prescriptions/preview` roda o
   *  MESMO `SchedulingService` sem gravar nada. */
  const [preview, setPreview] = useState<SchedulePreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (kind === "prn" || !name.trim()) {
      setPreview(null);
      setPreviewError(null);
      return;
    }
    const timer = setTimeout(() => {
      void api
        .previewSchedule({
          kind,
          category,
          name: name.trim(),
          frequency_minutes: frequency,
          duration_hours: duration ? Number(duration) : null,
          criticality,
          first_dose_now: firstDoseNow,
          starts_at: startsAt ? new Date(startsAt).toISOString() : null,
        })
        .then((value) => {
          setPreview(value);
          setPreviewError(null);
        })
        .catch((err) => {
          // Sem preview a prescrição ainda pode ser criada, mas quem prescreve
          // precisa saber que não está vendo os horários, e não olhar para um
          // quadro vazio achando que não haverá nenhum.
          setPreview(null);
          setPreviewError(describeError(err));
        });
    }, 250);
    return () => clearTimeout(timer);
  }, [kind, category, name, frequency, duration, criticality, firstDoseNow, startsAt, describeError]);

  async function submit() {
    if (!id) return;
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = {
      kind,
      category,
      name,
      criticality,
      first_dose_now: firstDoseNow,
      details: kind === "continuous" ? { rate_ml_h: Number(rate) } : {},
    };
    if (kind !== "prn") body.frequency_minutes = frequency;
    if (kind !== "prn" && duration) body.duration_hours = Number(duration);
    if (catalogItemId) body.price_list_item_id = catalogItemId;
    if (dosePerKg !== "" && dose) {
      // Guardamos a conta inteira, não só o resultado: a ficha mostra o volume
      // e o prontuário precisa saber de onde ele veio.
      body.details = {
        ...(body.details as Record<string, unknown> | undefined),
        dose_per_kg: decimal(dosePerKg),
        dose_mg: dose.dose_mg,
        volume_ml: dose.volume_ml,
        concentration_mg_per_ml: dose.concentration_mg_per_ml,
        weight_kg: dose.weight_kg,
      };
    }
    // Valor só vai no corpo quando o vet decide cobrar diferente do catálogo.
    if (overridePrice && price !== "") body.price_minor = Math.round(Number(price) * 100);
    else if (!catalogItemId && price !== "") body.price_minor = Math.round(Number(price) * 100);
    if (startsAt) body.starts_at = new Date(startsAt).toISOString();

    try {
      await api.createPrescription(id, body);
      navigate(`/internacao/${id}`);
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
      } else {
        setError(describeError(err));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <header className="prescribe-head">
        <Link to={`/internacao/${id}`} className="patient-back">
          {t("prescription.backToPatient", { name: patient?.name ?? "" })}
        </Link>
        <div className="patient-name-row">
          <h1 className="patient-name">{t("sheet.newPrescription")}</h1>
          {patient ? (
            <span className="prescribe-for">
              {t("prescription.forPatient", {
                name: patient.name,
                kennel: patient.kennel ?? "",
              })}
            </span>
          ) : null}
        </div>
      </header>
    <div className="prescribe-layout">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
        style={{ display: "grid", gap: 14, alignContent: "start" }}
      >
        <ErrorBanner message={error} />

        <Card style={{ display: "grid", gap: 14 }}>
          <Field label={t("prescription.category")}>
            <div className="chip-group">
              {CATEGORIES.map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={category === option}
                  onClick={() => {
                    setCategory(option);
                    setKind(KIND_BY_CATEGORY[option]);
                  }}
                  className={category === option ? "chip chip-on" : "chip"}
                >
                  {t(`prescription.category.${option}`)}
                </button>
              ))}
            </div>
          </Field>

          <Field label={t(`prescription.kindLabel.${category}`)}>
            <div className="chip-group">
              {perfil.kinds.map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={kind === option}
                  onClick={() => setKind(option)}
                  className={kind === option ? "chip chip-stacked chip-on" : "chip chip-stacked"}
                >
                  <span style={{ fontWeight: 600 }}>{t(`prescription.kind.${option}`)}</span>
                  <span className="chip-hint">
                    {t(`prescription.kindHint.${category}.${option}`, {
                      defaultValue: t(`prescription.kind.${option}Hint`),
                    })}
                  </span>
                </button>
              ))}
            </div>
          </Field>

          <Field label={t("prescription.name")}>
            <input
              style={inputStyle}
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
              placeholder={t(`prescription.namePlaceholder.${category}`)}
            />
          </Field>

          <Field label={t("prescription.fromCatalog")}>
            {catalog.length === 0 ? (
              <div style={{ fontSize: 13.5, color: "var(--ink-3)" }}>
                {t("prescription.catalogEmpty")}{" "}
                <Link to="/precos" style={{ color: "var(--primary-dark)" }}>
                  {t("prescription.catalogManage")}
                </Link>
              </div>
            ) : (
              <>
                <Combobox
                  value={catalogItemId}
                  emptyLabel={t("prescription.catalogNone")}
                  onChange={(value) => {
                    const chosen = catalog.find((item) => item.id === value);
                    setCatalogItemId(value);
                    setOverridePrice(false);
                    if (chosen) {
                      // Nome e categoria vêm do catálogo; o vet ajusta a dose depois.
                      if (!name) setName(chosen.name);
                      setCategory(chosen.category);
                    }
                  }}
                  options={catalog
                    .filter((item) => item.category === category || item.id === catalogItemId)
                    .map((item) => ({
                      value: item.id,
                      label: item.name,
                      // Código e preço desempatam itens homônimos sem lotar o rótulo principal.
                      hint: [item.code, money(item.price_minor)]
                        .filter(Boolean)
                        .join(" · "),
                    }))}
                />
                <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                  {t("prescription.fromCatalogHint")}
                </span>
              </>
            )}
          </Field>

          {catalogItemId && perfil.dosed ? (
            <DoseBlock
              dose={dose}
              value={dosePerKg}
              itemName={catalogItem?.name ?? null}
              onChange={(value) => {
                setDoseTouched(true);
                setDosePerKg(value);
              }}
            />
          ) : null}

          {kind !== "prn" ? (
            <div className="form-grid-2">
              <Field label={t("prescription.frequency")}>
                <Combobox
                  value={String(frequency)}
                  onChange={(value) => setFrequency(Number(value))}
                  options={FREQUENCIES.map((minutes) => ({
                    value: String(minutes),
                    label:
                      minutes % 60 === 0
                        ? t("sheet.frequency", { hours: minutes / 60 })
                        : t("sheet.frequencyMinutes", { minutes }),
                  }))}
                />
              </Field>
              <Field label={t("prescription.duration")}>
                <input
                  style={inputStyle}
                  type="number"
                  min={1}
                  value={duration}
                  onChange={(event) => setDuration(event.target.value)}
                />
              </Field>
            </div>
          ) : null}

          {kind === "continuous" ? (
            <Field label={t("prescription.rate")}>
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={rate}
                onChange={(event) => setRate(event.target.value)}
                required
              />
            </Field>
          ) : null}

          <Field label={t("prescription.startsAt")}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <input
                style={{ ...inputStyle, flex: "1 1 220px", width: "auto" }}
                type="datetime-local"
                value={startsAt}
                onChange={(event) => setStartsAt(event.target.value)}
              />
              <button
                type="button"
                className={startsAt === "" ? "chip chip-on" : "chip"}
                onClick={() => setStartsAt("")}
              >
                {t("prescription.startsAtNow")}
              </button>
            </div>
            <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
              {t("prescription.startsAtHint")}
            </span>
          </Field>

          <div className="form-grid-2">
            <Field label={t("prescription.criticality")}>
              <Combobox
                value={criticality}
                onChange={(value) => setCriticality(value as "normal" | "critical")}
                options={[
                  { value: "normal", label: t("prescription.criticality.normal") },
                  { value: "critical", label: t("prescription.criticality.critical") },
                ]}
              />
            </Field>
            <Field label={t("prescription.price")}>
              {catalogItem && !overridePrice ? (
                <div
                  style={{
                    ...inputStyle,
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span className="tabular" style={{ fontWeight: 600 }}>
                    {money(catalogItem.price_minor)}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                    {t("prescription.priceFromCatalog", { code: catalogItem.code ?? "–" })}
                  </span>
                </div>
              ) : (
                <input
                  style={inputStyle}
                  type="number"
                  min={0}
                  step="0.01"
                  value={price}
                  onChange={(event) => setPrice(event.target.value)}
                  placeholder={t("prescription.pricePlaceholder")}
                />
              )}
              {catalogItem ? (
                <label
                  style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5 }}
                >
                  <input
                    type="checkbox"
                    checked={overridePrice}
                    onChange={(event) => {
                      setOverridePrice(event.target.checked);
                      if (event.target.checked)
                        setPrice((catalogItem.price_minor / 100).toFixed(2));
                    }}
                    style={{ minHeight: 0, width: 16, height: 16 }}
                  />
                  {t("prescription.priceOverride")}
                </label>
              ) : null}
            </Field>
          </div>

          {kind !== "prn" ? (
            <label style={{ display: "flex", gap: 12, alignItems: "flex-start", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={firstDoseNow}
                onChange={(event) => setFirstDoseNow(event.target.checked)}
                style={{ minHeight: 0, width: 20, height: 20, marginTop: 2 }}
              />
              <span>
                <strong style={{ fontSize: 14.5 }}>
                  {t(`prescription.firstNow.${category}`)}
                </strong>
                <span style={{ display: "block", fontSize: 13, color: "var(--ink-3)" }}>
                  {t(`prescription.firstNowHint.${category}`)}
                </span>
              </span>
            </label>
          ) : null}
        </Card>

        <div style={{ display: "flex", gap: 10 }}>
          <Button type="submit" disabled={busy || !name}>
            {t("prescription.submit")}
          </Button>
          <Button variant="secondary" onClick={() => navigate(`/internacao/${id}`)}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>

      <Card style={{ alignSelf: "start" }}>
        <span className="eyebrow">{t("prescription.preview")}</span>
        {preview && preview.anchors.length > 0 ? (
          <p style={{ margin: "6px 0 0", fontSize: 12.5, color: "var(--ink-3)" }}>
            {t("prescription.previewAnchors", { anchors: preview.anchors.join(" / ") })}
          </p>
        ) : null}
        <div style={{ display: "grid", gap: 7, marginTop: 12 }}>
          {previewError ? (
            <span style={{ fontSize: 13.5, color: "var(--late)" }}>{previewError}</span>
          ) : kind === "prn" ? (
            <span style={{ fontSize: 13.5, color: "var(--ink-3)" }}>
              {t("prescription.previewPrn")}
            </span>
          ) : !preview ? (
            <span style={{ fontSize: 13.5, color: "var(--ink-3)" }}>
              {t("prescription.previewWaiting")}
            </span>
          ) : (
            preview.times.slice(0, 8).map((iso, index) => (
              <div
                key={iso}
                style={{
                  border: `1px solid ${firstDoseNow && index === 0 ? "var(--ok-edge)" : "var(--line)"}`,
                  background: firstDoseNow && index === 0 ? "var(--tint)" : "var(--surface)",
                  borderRadius: 8,
                  padding: "10px 12px",
                  fontSize: 14,
                  display: "flex",
                  justifyContent: "space-between",
                  gap: 10,
                }}
              >
                <span className="tabular" style={{ fontWeight: 600 }}>
                  {moment(iso)}
                </span>
                {firstDoseNow && index === 0 ? (
                  <span style={{ fontSize: 12.5, color: "var(--primary-dark)", fontWeight: 600 }}>
                    {t("prescription.firstDoseNow")}
                  </span>
                ) : null}
              </div>
            ))
          )}
        </div>
        {/* A supressão era calculada e jogada fora: quem prescreve via a
            segunda dose fora do horário-padrão e não sabia por quê. */}
        {preview && preview.suppressed > 0 ? (
          <p style={{ margin: "10px 0 0", fontSize: 12.5, color: "var(--ink-2)" }}>
            {t("prescription.previewSuppressed", { n: preview.suppressed })}
          </p>
        ) : null}
        {preview ? (
          <p style={{ margin: "6px 0 0", fontSize: 12.5, color: "var(--ink-3)" }}>
            {t("prescription.previewTolerance", { n: preview.tolerance_minutes })}
          </p>
        ) : null}
      </Card>

      {askPin ? (
        <PinDialog
          context={name}
          onDone={() => {
            setAskPin(false);
            void submit();
          }}
          onCancel={() => setAskPin(false)}
        />
      ) : null}
    </div>
    </>
  );
}


/** A conta da dose, exposta.
 *
 *  O campo já vem preenchido, e a linha de baixo mostra a aritmética inteira:
 *  o que o veterinário confere não é o número, é o caminho até ele. "0,27 ml"
 *  sozinho não se verifica; "0,15 mg/kg × 3,6 kg ÷ 2 mg/ml" se verifica num
 *  relance, e é isso que transforma o trabalho de calcular em conferir.
 *
 *  Tudo aqui AVISA e nada bloqueia. Fora da faixa, contraindicado na espécie,
 *  raça sensível: a decisão é de quem tem registro no conselho, e fricção sem
 *  valor clínico percebido é contornada: o sistema passaria a mentir. */
function DoseBlock({
  dose,
  value,
  itemName,
  onChange,
}: {
  dose: DosePreview | null;
  value: string;
  itemName: string | null;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const { number } = useClinic();
  if (!dose) return null;

  const grave = dose.warnings.includes("contraindicated");
  const atencao =
    dose.warnings.includes("above_range") ||
    dose.warnings.includes("below_range") ||
    dose.warnings.includes("breed_sensitivity") ||
    dose.warnings.includes("unreviewed_rule");

  return (
    <div className={`dose${grave ? " dose-danger" : atencao ? " dose-warn" : ""}`}>
      <div className="dose-row">
        <Field label={t("prescription.dosePerKg")}>
          <input
            style={inputStyle}
            inputMode="decimal"
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder={dose.dose_min_per_kg ? number(dose.dose_min_per_kg) : ""}
            aria-describedby="dose-math"
          />
          <span className="dose-adjust">{t("prescription.doseAdjustHint")}</span>
        </Field>

        {/* UM número grande: o que a pessoa aspira na seringa.
            Antes havia três competindo (mg/kg, mg e ml) e quem prescreve
            tinha de decidir qual deles era a resposta. O total em mg e a
            aritmética descem para a linha de conferência. */}
        <div className="dose-answer">
          <span className="eyebrow">{t("prescription.doseGive")}</span>
          {dose.volume_ml !== null ? (
            <>
              <strong className="tabular">
                {t("prescription.doseVolume", { ml: number(dose.volume_ml, 2) })}
              </strong>
              {itemName ? <span className="dose-of">{t("prescription.doseOf", { item: itemName })}</span> : null}
            </>
          ) : dose.dose_mg !== null ? (
            <>
              <strong className="tabular">
                {t("prescription.doseTotal", { mg: number(dose.dose_mg) })}
              </strong>
              {itemName ? <span className="dose-of">{t("prescription.doseOf", { item: itemName })}</span> : null}
            </>
          ) : (
            <strong className="dose-none">{t("prescription.doseUnknown")}</strong>
          )}
        </div>
      </div>

      {/* A conferência: a conta inteira, pequena. É ela que se lê para checar,
          não para decidir. */}
      {dose.dose_mg !== null && dose.weight_kg !== null ? (
        <p className="dose-math tabular" id="dose-math">
          {dose.warnings.includes("fixed_dose")
            ? t("prescription.doseMathFixed", {
                mg: number(dose.dose_mg),
                conc: number(dose.concentration_mg_per_ml),
              })
            : t("prescription.doseMath", {
                perKg: number(dose.dose_per_kg),
                weight: number(dose.weight_kg),
                mg: number(dose.dose_mg),
                conc: number(dose.concentration_mg_per_ml),
              })}
        </p>
      ) : null}

      <p className="dose-provenance">
        {dose.dose_min_per_kg && dose.dose_max_per_kg
          ? t("prescription.doseRange", {
              species: dose.species ?? "",
              min: number(dose.dose_min_per_kg),
              max: number(dose.dose_max_per_kg),
            })
          : null}
        {dose.reviewed && dose.reviewed_by_name
          ? ` · ${t("prescription.doseReviewed", { name: dose.reviewed_by_name })}`
          : null}
      </p>

      {dose.warnings
        .filter((code) => code !== "fixed_dose")
        .map((code) => (
          <p key={code} className="dose-warning">
            {t(`prescription.doseWarning.${code}`)}
          </p>
        ))}
      {/* Texto escrito pela clínica: não é traduzido (spec §3.6). */}
      {dose.notes.map((note) => (
        <p key={note} className="dose-warning">
          {note}
        </p>
      ))}
    </div>
  );
}
