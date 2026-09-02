import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { CATEGORIES, CATEGORY_PROFILE } from "../api/categories";
import { api, asList } from "../api/client";
import type { DoseRule, PrescriptionCategory, PriceListItem } from "../api/types";
import { CAN } from "../api/capabilities";
import { AdminModal, AdminNote, CheckRow, usePinRetry } from "../components/AdminShared";
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
  decimal,
  decimalField,
  inputStyle,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { useSession } from "../hooks/useSession";
import "../styles/admin.css";

/** "18,50" ou "18.50" → 1850. O preço trafega em unidade menor (centavos). */
function toMinor(value: string): number | null {
  const normalized = value.replace(/\s/g, "").replace(",", ".");
  if (!/^\d+(\.\d{0,2})?$/.test(normalized)) return null;
  return Math.round(Number(normalized) * 100);
}

/** O preço no formato em que se digita. O separador é o do locale da clínica:
 *  "18.00" ao lado de uma concentração "500,5" na mesma tela é a interface
 *  falando duas línguas de número de uma vez. `toMinor` aceita as duas. */
function toMajor(minor: number, separator: string): string {
  return (minor / 100).toFixed(2).replace(".", separator);
}

/** Campo numérico vazio vira `null`, não `0`.
 *
 *  Zero e "não informado" são coisas diferentes numa posologia: dose máxima
 *  zero diria que o fármaco não pode ser dado. */
function numberOrNull(value: string): string | null {
  const normalized = decimal(value);
  if (!normalized) return null;
  if (!/^\d+(\.\d+)?$/.test(normalized)) return null;
  return normalized;
}

interface ItemForm {
  code: string;
  name: string;
  category: PrescriptionCategory;
  unit: string;
  price: string;
  concentration: string;
  is_daily_rate: boolean;
  kennel_area: string;
  is_controlled: boolean;
  is_active: boolean;
}

/** O ponto de partida quando a tabela ainda está vazia: sem histórico, um
 *  seletor sem opção nenhuma é pior que um campo de texto. */
const DEFAULT_UNITS = ["por dose", "por aplicação", "por bolsa", "por dia", "por sessão"];

const EMPTY_FORM: ItemForm = {
  code: "",
  name: "",
  category: "medication",
  unit: "",
  price: "",
  concentration: "",
  is_daily_rate: false,
  kennel_area: "",
  is_controlled: false,
  is_active: true,
};

function toForm(item: PriceListItem, separator: string): ItemForm {
  return {
    code: item.code ?? "",
    name: item.name,
    category: item.category,
    unit: item.unit ?? "",
    price: toMajor(item.price_minor, separator),
    concentration: decimalField(item.concentration_mg_per_ml, separator),
    is_daily_rate: item.is_daily_rate,
    kennel_area: item.kennel_area ?? "",
    is_controlled: item.is_controlled,
    is_active: item.is_active,
  };
}

export function PriceList() {
  const { t } = useTranslation();
  const { run, dialog, error, busy, describeError } = usePinRetry();
  // Moeda e formatação vêm da clínica. Estava `"BRL"` cravado aqui enquanto as
  // configurações ofereciam USD e EUR: a clínica trocava a moeda e a tabela de
  // preços continuava dizendo "R$".
  const { money, decimalSeparator } = useClinic();

  // `null` é "ainda carregando". Uma lista vazia por falha de rede seria lida
  // como "esta clínica não tem preço cadastrado", e alguém cadastraria tudo
  // de novo.
  const [items, setItems] = useState<PriceListItem[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [includeInactive, setIncludeInactive] = useState(false);
  const [filter, setFilter] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<PriceListItem | null>(null);
  const [creating, setCreating] = useState(false);

  /** As unidades que a clínica JÁ usa.
   *
   *  Era campo aberto, e campo aberto no mesmo conceito vira dialeto: "por
   *  dose", "por Dose", "dose" e "por aplicação" convivendo na mesma tabela,
   *  todas impressas na conta do tutor. Escolher da lista é um clique; criar
   *  uma unidade nova continua possível. */
  const units = useMemo(
    () =>
      [
        ...new Set(
          (items ?? []).map((item) => item.unit).filter((unit): unit is string => !!unit),
        ),
      ].sort((a, b) => a.localeCompare(b)),
    [items],
  );

  /** As áreas dos BOXES, não as já digitadas na tabela de preços. */
  const [areas, setAreas] = useState<string[]>([]);
  const [areasError, setAreasError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setItems(asList(await api.priceList(includeInactive)));
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [includeInactive, describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let alive = true;
    // A diária é escolhida por igualdade EXATA entre `kennel_area` do item e
    // `area` do box (`ChargesService`). A sugestão vinha dos próprios itens de
    // preço, então um "UTI " com espaço se propagava sozinho e a diária da UTI
    // simplesmente parava de ser lançada, sem erro nenhum. A lista de áreas de
    // verdade é a dos boxes.
    void api
      .kennels(true)
      .then((page) => {
        if (!alive) return;
        setAreas(
          [
            ...new Set(
              asList(page)
                .map((kennel) => kennel.area)
                .filter((area): area is string => !!area),
            ),
          ].sort(),
        );
        setAreasError(null);
      })
      .catch((err) => {
        // Sem as áreas o campo continua utilizável (é texto), mas quem digita
        // precisa saber que está sem rede de proteção contra erro de digitação.
        if (alive) setAreasError(describeError(err));
      });
    return () => {
      alive = false;
    };
  }, [describeError]);

  const list = useMemo(() => items ?? [], [items]);

  const counts = useMemo(() => {
    const map: Record<string, number> = { all: list.length, daily: 0 };
    for (const item of list) {
      map[item.category] = (map[item.category] ?? 0) + 1;
      if (item.is_daily_rate) map.daily += 1;
    }
    return map;
  }, [list]);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return list.filter((item) => {
      if (filter === "daily" && !item.is_daily_rate) return false;
      if (filter !== "all" && filter !== "daily" && item.category !== filter) return false;
      if (!needle) return true;
      return (
        item.name.toLowerCase().includes(needle) || (item.code ?? "").toLowerCase().includes(needle)
      );
    });
  }, [list, filter, search]);

  function bodyOf(form: ItemForm): Record<string, unknown> | null {
    const minor = toMinor(form.price);
    if (minor === null || !form.name.trim() || !form.unit.trim()) return null;
    const dosed = CATEGORY_PROFILE[form.category].dosed && !form.is_daily_rate;
    return {
      code: form.code.trim() || null,
      name: form.name.trim(),
      category: form.category,
      unit: form.unit.trim(),
      price_minor: minor,
      is_daily_rate: form.is_daily_rate,
      kennel_area: form.is_daily_rate ? form.kennel_area.trim() || null : null,
      // Campo que a tela não mostra não pode continuar valendo por baixo: um
      // item marcado como controlado que vira diária ficaria controlado para
      // sempre, sem nada na interface dizendo isso.
      is_controlled: form.category === "medication" && !form.is_daily_rate && form.is_controlled,
      concentration_mg_per_ml: dosed ? numberOrNull(form.concentration) : null,
    };
  }

  async function create(form: ItemForm) {
    const body = bodyOf(form);
    if (!body) return;
    await run(async () => {
      const created = await api.createPriceListItem(body);
      setCreating(false);
      await load();
      // O próximo passo de um fármaco recém-cadastrado é a posologia, e ela só
      // existe depois que o item tem id. Em vez de fechar e obrigar a procurar
      // o item de novo na tabela, o sistema já abre onde se continua.
      if (CATEGORY_PROFILE[form.category].dosed && !form.is_daily_rate) setEditing(created);
    });
  }

  async function update(item: PriceListItem, form: ItemForm) {
    const body = bodyOf(form);
    if (!body) return;
    await run(async () => {
      await api.updatePriceListItem(item.id, {
        ...body,
        is_active: form.is_active,
      });
      setEditing(null);
      await load();
    });
  }

  const filters = ["all", "daily", ...CATEGORIES];

  return (
    <>
      {error ? <ErrorState message={error} /> : null}

      <Section
        title={t("pricing.title")}
        hint={t("pricing.subtitle")}
        actions={
          <Gate can={CAN.priceListManage}>
            <Button onClick={() => setCreating(true)}>{t("pricing.new")}</Button>
          </Gate>
        }
      >
        {loadError ? <ErrorState message={loadError} onRetry={() => void load()} /> : null}
        {!loadError && items === null ? <Skeleton rows={5} height={48} /> : null}

        {!loadError && items !== null ? (
          <Card style={{ display: "grid", gap: 14 }}>
            <div className="admin-toolbar">
              <input
                type="search"
                style={inputStyle}
                placeholder={t("pricing.search")}
                aria-label={t("pricing.search")}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <button
                type="button"
                aria-pressed={includeInactive}
                className={includeInactive ? "chip chip-on" : "chip"}
                onClick={() => setIncludeInactive((current) => !current)}
              >
                {t("pricing.showInactive")}
              </button>
            </div>

            <div className="chip-group">
              {filters.map((option) => (
                <button
                  key={option}
                  type="button"
                  aria-pressed={filter === option}
                  className={filter === option ? "chip chip-on" : "chip"}
                  onClick={() => setFilter(option)}
                >
                  {option === "all"
                    ? t("pricing.filter.all")
                    : option === "daily"
                      ? t("pricing.filter.daily")
                      : t(`prescription.category.${option}`)}
                  {counts[option] ? ` · ${counts[option]}` : ""}
                </button>
              ))}
            </div>

            {visible.length === 0 ? (
              <EmptyState
                title={list.length === 0 ? t("pricing.emptyAll") : t("pricing.empty")}
                hint={list.length === 0 ? t("pricing.emptyHint") : t("pricing.emptyFilterHint")}
                action={
                  list.length === 0 ? (
                    <Gate can={CAN.priceListManage}>
                      <Button onClick={() => setCreating(true)}>{t("pricing.new")}</Button>
                    </Gate>
                  ) : null
                }
              />
            ) : (
              <div className="admin-table-wrap">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>{t("pricing.col.code")}</th>
                      <th>{t("pricing.col.item")}</th>
                      <th>{t("pricing.col.category")}</th>
                      <th>{t("pricing.col.unit")}</th>
                      <th>{t("pricing.col.price")}</th>
                      <th>{t("pricing.col.dosing")}</th>
                      <th>{t("pricing.col.daily")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visible.map((item) => (
                      <tr
                        key={item.id}
                        className="admin-row-click"
                        onClick={() => setEditing(item)}
                        tabIndex={0}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") setEditing(item);
                        }}
                      >
                        <td className="tabular admin-cell-muted">{item.code ?? "–"}</td>
                        <td>
                          <span style={{ fontWeight: 600 }}>{item.name}</span>
                          <span
                            style={{
                              display: "inline-flex",
                              gap: 6,
                              marginLeft: 8,
                            }}
                          >
                            {item.is_controlled ? (
                              <span className="admin-badge admin-badge-warn">
                                {t("pricing.controlled")}
                              </span>
                            ) : null}
                            {!item.is_active ? (
                              <span className="admin-badge admin-badge-off">
                                {t("pricing.inactive")}
                              </span>
                            ) : null}
                          </span>
                        </td>
                        <td>{t(`prescription.category.${item.category}`)}</td>
                        <td className="admin-cell-muted">{item.unit ?? "–"}</td>
                        <td className="tabular" style={{ fontWeight: 600 }}>
                          {money(item.price_minor)}
                        </td>
                        {/* A pergunta que a tabela não respondia: quais
                            fármacos já calculam dose. Sem posologia conferida
                            a calculadora abre vazia na hora de prescrever, e
                            só se descobria abrindo item por item. */}
                        <td>
                          {!CATEGORY_PROFILE[item.category].dosed || item.is_daily_rate ? (
                            <span className="admin-cell-muted">–</span>
                          ) : item.reviewed_dose_rules > 0 ? (
                            <span className="admin-badge admin-badge-on">
                              {t("pricing.dosingCount", { count: item.reviewed_dose_rules })}
                            </span>
                          ) : (
                            <span className="admin-badge admin-badge-warn">
                              {t("pricing.noDosing")}
                            </span>
                          )}
                        </td>
                        <td>
                          {item.is_daily_rate ? (
                            <span className="admin-badge admin-badge-on">
                              {item.kennel_area
                                ? t("pricing.dailyArea", {
                                    area: item.kennel_area,
                                  })
                                : t("pricing.daily")}
                            </span>
                          ) : (
                            <span className="admin-cell-muted">–</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <p className="admin-footnote">
              {t("pricing.showing", {
                shown: visible.length,
                total: list.length,
              })}
            </p>
          </Card>
        ) : null}

        <p className="admin-footnote">{t("pricing.footer")}</p>
      </Section>

      {creating ? (
        <ItemDialog
          title={t("pricing.new")}
          initial={EMPTY_FORM}
          item={null}
          areas={areas}
          units={units}
          areasError={areasError}
          busy={busy}
          run={run}
          onClose={() => setCreating(false)}
          onSubmit={(form) => void create(form)}
        />
      ) : null}

      {editing ? (
        <ItemDialog
          title={editing.name}
          initial={toForm(editing, decimalSeparator)}
          item={editing}
          areas={areas}
          units={units}
          areasError={areasError}
          busy={busy}
          run={run}
          onClose={() => setEditing(null)}
          onSubmit={(form) => void update(editing, form)}
        />
      ) : null}

      {dialog}
    </>
  );
}

function ItemDialog({
  title,
  initial,
  item,
  areas,
  units,
  areasError,
  busy,
  run,
  onClose,
  onSubmit,
}: {
  title: string;
  initial: ItemForm;
  /** `null` enquanto o item não existe: sem id não há posologia para pendurar. */
  item: PriceListItem | null;
  areas: string[];
  /** As unidades já usadas nesta clínica. */
  units: string[];
  areasError: string | null;
  busy: boolean;
  run: (action: () => Promise<void>) => Promise<void>;
  onClose: () => void;
  onSubmit: (form: ItemForm) => void;
}) {
  const { t } = useTranslation();
  const { currency } = useClinic();
  const [form, setForm] = useState<ItemForm>(initial);

  function patch(next: Partial<ItemForm>) {
    setForm((current) => ({ ...current, ...next }));
  }

  const valid = form.name.trim() && form.unit.trim() && toMinor(form.price) !== null;

  /** Só o que se dosa pergunta concentração e posologia. Uma diária de UTI não
   *  tem mg/ml, e o campo ali era uma pergunta sem resposta possível. */
  const dosed = CATEGORY_PROFILE[form.category].dosed && !form.is_daily_rate;
  /** Controlado é atributo de fármaco. Aparecia numa diária de internação. */
  const controllable = form.category === "medication" && !form.is_daily_rate;

  // Uma área digitada que nenhum box usa nunca casa com box nenhum: a diária
  // simplesmente não é lançada. Vale escolher da lista, ou saber o que se faz.
  const unknownArea =
    form.is_daily_rate &&
    form.kennel_area.trim() !== "" &&
    !areas.includes(form.kennel_area.trim());

  return (
    <AdminModal title={title} onClose={onClose} wide={!dosed} xwide={dosed}>
      <div className={dosed ? "item-layout" : undefined}>
        <div className="item-col">
          <div className="form-grid-2">
            <Field label={t("pricing.form.code")}>
              <input
                style={inputStyle}
                value={form.code}
                placeholder="MED-018"
                onChange={(event) => patch({ code: event.target.value })}
              />
            </Field>
            <Field label={t("pricing.form.category")}>
              <Combobox
                value={form.category}
                onChange={(value) => patch({ category: value as PrescriptionCategory })}
                options={CATEGORIES.map((category) => ({
                  value: category,
                  label: t(`prescription.category.${category}`),
                }))}
              />
            </Field>
          </div>

          <Field label={t("pricing.form.name")}>
            <input
              style={inputStyle}
              value={form.name}
              onChange={(event) => patch({ name: event.target.value })}
            />
          </Field>

          <div className="form-grid-2">
            <Field label={t("pricing.form.unit")}>
              <Combobox
                value={form.unit}
                onChange={(value) => patch({ unit: value })}
                options={(units.length > 0 ? units : DEFAULT_UNITS).map((unit) => ({
                  value: unit,
                  label: unit,
                }))}
                placeholder={t("pricing.form.unitPick")}
                onCreate={(typed) => patch({ unit: typed })}
                createLabel={(typed) => t("pricing.form.unitCreate", { unit: typed })}
              />
            </Field>
            <Field label={t("pricing.form.price")}>
              <input
                style={inputStyle}
                className="tabular"
                inputMode="decimal"
                value={form.price}
                placeholder="18,00"
                onChange={(event) => patch({ price: event.target.value })}
              />
              <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                {/* A moeda é da clínica: quem digita precisa saber em qual está. */}
                {t("pricing.form.priceCurrencyHint", { currency })}
              </span>
            </Field>
          </div>

          {dosed ? (
            <Field label={t("pricing.form.concentration")}>
              <input
                style={inputStyle}
                className="tabular"
                inputMode="decimal"
                value={form.concentration}
                placeholder="500"
                onChange={(event) => patch({ concentration: event.target.value })}
              />
              <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                {t("pricing.form.concentrationHint")}
              </span>
            </Field>
          ) : null}

          <CheckRow
            checked={form.is_daily_rate}
            onChange={(value) => patch({ is_daily_rate: value })}
            label={t("pricing.form.isDaily")}
            hint={t("pricing.form.isDailyHint")}
          />

          {form.is_daily_rate ? (
            <>
              <Field label={t("pricing.form.kennelArea")}>
                <Combobox
                  value={form.kennel_area}
                  onChange={(value) => patch({ kennel_area: value })}
                  options={areas.map((area) => ({ value: area, label: area }))}
                  emptyLabel={t("pricing.form.anyArea")}
                  placeholder={t("pricing.form.kennelAreaPick")}
                  onCreate={(typed) => patch({ kennel_area: typed })}
                  createLabel={(typed) => t("pricing.form.kennelAreaCreate", { area: typed })}
                />
                <span style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                  {t("pricing.form.kennelAreaHint")}
                </span>
              </Field>
              {/* Falha ao ler os boxes não pode virar "esta clínica não tem área":
              sem a lista, quem digita fica sem proteção contra erro de digitação
              e precisa saber disso. */}
              {areasError ? <AdminNote tone="danger">{areasError}</AdminNote> : null}
              {unknownArea ? <AdminNote>{t("pricing.form.kennelAreaUnknown")}</AdminNote> : null}
            </>
          ) : null}

          {controllable ? (
            <CheckRow
              checked={form.is_controlled}
              onChange={(value) => patch({ is_controlled: value })}
              label={t("pricing.form.isControlled")}
              hint={t("pricing.form.isControlledHint")}
            />
          ) : null}

          {item ? (
            <CheckRow
              checked={form.is_active}
              onChange={(value) => patch({ is_active: value })}
              label={t("pricing.form.isActive")}
              hint={t("pricing.form.isActiveHint")}
            />
          ) : null}

          {item ? <AdminNote>{t("pricing.priceWarning")}</AdminNote> : null}

          <div className="admin-toolbar">
            <Button disabled={!valid || busy} onClick={() => onSubmit(form)}>
              {t("pricing.form.save")}
            </Button>
            <Button variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
          </div>
        </div>

        {dosed ? (
          <DoseRules
            item={item}
            concentration={numberOrNull(form.concentration)}
            run={run}
            busy={busy}
          />
        ) : null}
      </div>
    </AdminModal>
  );
}

/* --------------------------------------------------------------------------
 * Posologia
 *
 * Mora dentro do item de preço porque é lá que o fármaco já é definido uma
 * vez. Uma tela separada de "posologias" seria um segundo cadastro do mesmo
 * remédio, e a divergência entre os dois é só questão de tempo: alguém
 * corrigiria a dose num lugar e a calculadora continuaria lendo o outro.
 * -------------------------------------------------------------------------- */

/** Espécies sugeridas. É só sugestão: espécie é texto livre no cadastro do
 *  paciente, e a clínica de exóticos escreve a dela. */
const SPECIES_SUGGESTIONS = ["Canino", "Felino"];

/** As vias de administração da prática clínica.
 *
 *  Lista, e não campo aberto: a calculadora casa a via por igualdade, e "IV",
 *  "iv" e "E.V." digitados à mão viravam três regras diferentes para a mesma
 *  coisa. Criar uma via nova continua possível, para quem trabalha com algo
 *  fora desta lista, só deixa de ser o caminho padrão. */
const ROUTES = ["IV", "IM", "SC", "VO", "IO", "IN", "IP", "TOP", "RET", "OFT", "OTO"];
const FREQUENCIES = [60, 120, 240, 360, 480, 720, 1440];

interface RuleForm {
  /** Regra que já está no banco: só ela pode ser removida, e a remoção mora
   *  dentro dela. Um botão "remover a última" na lista removeria uma regra
   *  escolhida pela ordem de leitura, não por quem está olhando. */
  existing: boolean;
  species: string;
  route: string;
  mode: "per_kg" | "fixed";
  dose_min_per_kg: string;
  dose_default_per_kg: string;
  dose_max_per_kg: string;
  fixed_dose_mg: string;
  max_total_mg: string;
  frequency_minutes: string;
  is_contraindicated: boolean;
  warning: string;
  breeds: string;
  breed_warning: string;
  source: string;
  reviewed: boolean;
}

const EMPTY_RULE: RuleForm = {
  existing: false,
  species: "",
  route: "",
  mode: "per_kg",
  dose_min_per_kg: "",
  dose_default_per_kg: "",
  dose_max_per_kg: "",
  fixed_dose_mg: "",
  max_total_mg: "",
  frequency_minutes: "",
  is_contraindicated: false,
  warning: "",
  breeds: "",
  breed_warning: "",
  source: "",
  reviewed: false,
};

function ruleToForm(rule: DoseRule, separator: string): RuleForm {
  return {
    existing: true,
    species: rule.species ?? "",
    route: rule.route ?? "",
    mode: rule.fixed_dose_mg ? "fixed" : "per_kg",
    dose_min_per_kg: decimalField(rule.dose_min_per_kg, separator),
    dose_default_per_kg: decimalField(rule.dose_default_per_kg, separator),
    dose_max_per_kg: decimalField(rule.dose_max_per_kg, separator),
    fixed_dose_mg: decimalField(rule.fixed_dose_mg, separator),
    max_total_mg: decimalField(rule.max_total_mg, separator),
    frequency_minutes: rule.frequency_minutes ? String(rule.frequency_minutes) : "",
    is_contraindicated: rule.is_contraindicated,
    warning: rule.warning ?? "",
    breeds: rule.breeds ?? "",
    breed_warning: rule.breed_warning ?? "",
    source: rule.source ?? "",
    // Nunca pré-marcado: conferir é um ato, e reabrir a regra não reconfere
    // nada. O selo antigo continua aparecendo ao lado, com nome e data.
    reviewed: false,
  };
}

function DoseRules({
  item,
  concentration,
  run,
  busy,
}: {
  item: PriceListItem | null;
  concentration: string | null;
  run: (action: () => Promise<void>) => Promise<void>;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const { day, number, decimalSeparator } = useClinic();
  const { can } = useSession();

  const [rules, setRules] = useState<DoseRule[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<RuleForm | null>(null);

  const itemId = item?.id ?? null;

  const load = useCallback(async () => {
    if (!itemId) return;
    try {
      setRules(await api.doseRules(itemId));
      setError(null);
    } catch {
      setError(t("pricing.dosing.loadFailed"));
    }
  }, [itemId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!itemId) {
    // Sem id não há onde pendurar a regra. Dizer isso é melhor do que mostrar
    // um bloco morto: quem cadastra sabe que a posologia vem no passo seguinte,
    // e o passo seguinte se abre sozinho ao salvar.
    return (
      <section className="dose-rules">
        <h3>{t("pricing.dosing.title")}</h3>
        <p className="admin-footnote">{t("pricing.dosing.afterSave")}</p>
      </section>
    );
  }

  /** O corpo do upsert. A chave é (espécie, via): é ela que decide entre criar
   *  e atualizar, tanto ao salvar quanto ao desativar. */
  function bodyOfRule(form: RuleForm, isActive: boolean): Record<string, unknown> {
    const fixed = form.mode === "fixed";
    return {
      species: form.species.trim() || null,
      route: form.route.trim() || null,
      // Um modo zera o outro: guardar mg/kg e dose fixa na mesma regra deixaria
      // a calculadora escolher, e a dose fixa vence. O número esquecido no
      // campo errado viraria uma dose que ninguém pediu.
      dose_min_per_kg: fixed ? null : numberOrNull(form.dose_min_per_kg),
      dose_default_per_kg: fixed ? null : numberOrNull(form.dose_default_per_kg),
      dose_max_per_kg: fixed ? null : numberOrNull(form.dose_max_per_kg),
      fixed_dose_mg: fixed ? numberOrNull(form.fixed_dose_mg) : null,
      max_total_mg: numberOrNull(form.max_total_mg),
      frequency_minutes: form.frequency_minutes ? Number(form.frequency_minutes) : null,
      is_contraindicated: form.is_contraindicated,
      warning: form.warning.trim() || null,
      breeds: form.breeds.trim() || null,
      breed_warning: form.breed_warning.trim() || null,
      source: form.source.trim() || null,
      reviewed: form.reviewed,
      is_active: isActive,
    };
  }

  async function save(form: RuleForm) {
    if (!itemId) return;
    await run(async () => {
      await api.saveDoseRule(itemId, bodyOfRule(form, true));
      setEditing(null);
      await load();
    });
  }

  /** Desliga em vez de apagar: a posologia já pode ter sido usada em
   *  prescrições que continuam no prontuário, e o histórico precisa continuar
   *  explicável. Os números vão junto, intactos: uma regra desligada que
   *  perdesse os valores voltaria vazia se alguém a reativasse. */
  async function deactivate(form: RuleForm) {
    if (!itemId) return;
    await run(async () => {
      await api.saveDoseRule(itemId, {
        ...bodyOfRule(form, false),
        reviewed: false,
      });
      setEditing(null);
      await load();
    });
  }

  const active = (rules ?? []).filter((rule) => rule.is_active);
  const canReview = can(CAN.prescriptionCreate);

  if (editing) {
    return (
      <RuleEditor
        form={editing}
        canReview={canReview}
        busy={busy}
        onChange={setEditing}
        onCancel={() => setEditing(null)}
        onSave={() => void save(editing)}
        onRemove={() => {
          if (window.confirm(t("pricing.dosing.confirmRemove"))) void deactivate(editing);
        }}
      />
    );
  }

  return (
    <section className="dose-rules">
      <h3>{t("pricing.dosing.title")}</h3>
      <p className="dose-rules-lead">{t("pricing.dosing.lead")}</p>

      {/* A posologia calcula até mg. Sem mg/ml ela não vira volume, e quem
          administra recebe "0,54 mg" sem saber quanto puxar na seringa. */}
      {!concentration ? <AdminNote>{t("pricing.dosing.needsConcentration")}</AdminNote> : null}
      {error ? <AdminNote tone="danger">{error}</AdminNote> : null}

      {rules === null ? <Skeleton rows={2} height={56} /> : null}

      {rules !== null && active.length === 0 ? (
        <p className="admin-footnote">{t("pricing.dosing.none")}</p>
      ) : null}

      {active.map((rule) => (
        <button
          key={rule.id}
          type="button"
          className="dose-rule"
          onClick={() => setEditing(ruleToForm(rule, decimalSeparator))}
        >
          <span className="dose-rule-species">
            {rule.species ?? t("pricing.dosing.anySpecies")}
            {rule.route ? <em>{rule.route}</em> : null}
          </span>
          <span className="dose-rule-what tabular">
            {rule.is_contraindicated
              ? t("pricing.dosing.contraindicated")
              : rule.fixed_dose_mg
                ? t("pricing.dosing.fixedSummary", {
                    mg: number(rule.fixed_dose_mg, 3),
                  })
                : t("pricing.dosing.rangeSummary", {
                    min: number(rule.dose_min_per_kg ?? rule.dose_default_per_kg ?? "0", 3),
                    max: number(rule.dose_max_per_kg ?? rule.dose_default_per_kg ?? "0", 3),
                  })}
            {rule.frequency_minutes
              ? ` · ${
                  rule.frequency_minutes % 60 === 0
                    ? t("sheet.frequency", {
                        hours: rule.frequency_minutes / 60,
                      })
                    : t("sheet.frequencyMinutes", {
                        minutes: rule.frequency_minutes,
                      })
                }`
              : ""}
          </span>
          {/* Regra sem conferência não pré-preenche nada na prescrição. Dizer
              isso aqui é a diferença entre um cadastro que funciona e um que
              parece funcionar. */}
          <span className={rule.reviewed_at ? "dose-rule-seal" : "dose-rule-unsealed"}>
            {rule.reviewed_at
              ? t("pricing.dosing.reviewedBy", {
                  name: rule.reviewed_by_name ?? "",
                  date: day(rule.reviewed_at),
                })
              : t("pricing.dosing.unreviewed")}
          </span>
        </button>
      ))}

      <div className="admin-toolbar">
        <Button variant="secondary" onClick={() => setEditing({ ...EMPTY_RULE })}>
          {t("pricing.dosing.add")}
        </Button>
      </div>
    </section>
  );
}

function RuleEditor({
  form,
  canReview,
  busy,
  onChange,
  onCancel,
  onSave,
  onRemove,
}: {
  form: RuleForm;
  canReview: boolean;
  busy: boolean;
  onChange: (next: RuleForm) => void;
  onCancel: () => void;
  onSave: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();

  function patch(next: Partial<RuleForm>) {
    onChange({ ...form, ...next });
  }

  const fixed = form.mode === "fixed";
  const temNumero = fixed
    ? numberOrNull(form.fixed_dose_mg) !== null
    : numberOrNull(form.dose_default_per_kg) !== null ||
      numberOrNull(form.dose_min_per_kg) !== null;
  const valid = form.is_contraindicated ? form.warning.trim().length > 0 : temNumero;

  return (
    <section className="dose-rules">
      <h3>{form.existing ? t("pricing.dosing.editTitle") : t("pricing.dosing.newTitle")}</h3>

      <div className="form-grid-2">
        <Field label={t("pricing.dosing.species")}>
          <Combobox
            value={form.species}
            onChange={(value) => patch({ species: value })}
            options={SPECIES_SUGGESTIONS.map((s) => ({ value: s, label: s }))}
            emptyLabel={t("pricing.dosing.anySpecies")}
            onCreate={(typed) => patch({ species: typed })}
            createLabel={(typed) => t("pricing.dosing.speciesCreate", { species: typed })}
          />
          <span className="dose-hint">{t("pricing.dosing.speciesHint")}</span>
        </Field>
        <Field label={t("pricing.dosing.route")}>
          <Combobox
            value={form.route}
            onChange={(value) => patch({ route: value })}
            options={ROUTES.map((route) => ({ value: route, label: route }))}
            emptyLabel={t("pricing.dosing.anyRoute")}
            placeholder={t("pricing.dosing.routePick")}
            onCreate={(typed) => patch({ route: typed })}
            createLabel={(typed) => t("pricing.dosing.routeCreate", { route: typed })}
          />
        </Field>
      </div>

      <CheckRow
        checked={form.is_contraindicated}
        onChange={(value) => patch({ is_contraindicated: value })}
        label={t("pricing.dosing.contraindicatedLabel")}
        hint={t("pricing.dosing.contraindicatedHint")}
      />

      {form.is_contraindicated ? (
        // Contraindicado não tem faixa: mostrar campos de dose ao lado disso
        // seria oferecer a conta de algo que a regra acabou de proibir.
        <Field label={t("pricing.dosing.warning")}>
          <input
            style={inputStyle}
            value={form.warning}
            placeholder={t("pricing.dosing.contraindicatedPlaceholder")}
            onChange={(event) => patch({ warning: event.target.value })}
          />
        </Field>
      ) : (
        <>
          <Field label={t("pricing.dosing.mode")}>
            <div className="chip-group">
              <button
                type="button"
                aria-pressed={!fixed}
                className={!fixed ? "chip chip-on" : "chip"}
                onClick={() => patch({ mode: "per_kg" })}
              >
                {t("pricing.dosing.modePerKg")}
              </button>
              <button
                type="button"
                aria-pressed={fixed}
                className={fixed ? "chip chip-on" : "chip"}
                onClick={() => patch({ mode: "fixed" })}
              >
                {t("pricing.dosing.modeFixed")}
              </button>
            </div>
            <span className="dose-hint">
              {fixed ? t("pricing.dosing.modeFixedHint") : t("pricing.dosing.modePerKgHint")}
            </span>
          </Field>

          {fixed ? (
            <Field label={t("pricing.dosing.fixedDose")}>
              <input
                style={inputStyle}
                className="tabular"
                inputMode="decimal"
                value={form.fixed_dose_mg}
                placeholder="2"
                onChange={(event) => patch({ fixed_dose_mg: event.target.value })}
              />
            </Field>
          ) : (
            /* A unidade sai dos três rótulos e sobe para o grupo. Repetida em
               cada um, ela empurrava "Mínima (mg/kg)" para duas linhas ao lado
               de "Padrão (mg/kg)" numa só, e os três campos desalinhavam
               verticalmente por causa disso. */
            <div className="dose-range">
              <span className="dose-range-label">{t("pricing.dosing.range")}</span>
              <div className="form-grid-3">
                <Field label={t("pricing.dosing.min")}>
                  <input
                    style={inputStyle}
                    className="tabular"
                    inputMode="decimal"
                    value={form.dose_min_per_kg}
                    placeholder="0,1"
                    onChange={(event) => patch({ dose_min_per_kg: event.target.value })}
                  />
                </Field>
                <Field label={t("pricing.dosing.default")}>
                  <input
                    style={inputStyle}
                    className="tabular"
                    inputMode="decimal"
                    value={form.dose_default_per_kg}
                    placeholder="0,2"
                    onChange={(event) => patch({ dose_default_per_kg: event.target.value })}
                  />
                </Field>
                <Field label={t("pricing.dosing.max")}>
                  <input
                    style={inputStyle}
                    className="tabular"
                    inputMode="decimal"
                    value={form.dose_max_per_kg}
                    placeholder="0,3"
                    onChange={(event) => patch({ dose_max_per_kg: event.target.value })}
                  />
                </Field>
              </div>
            </div>
          )}

          <div className="form-grid-2">
            <Field label={t("pricing.dosing.maxTotal")}>
              <input
                style={inputStyle}
                className="tabular"
                inputMode="decimal"
                value={form.max_total_mg}
                onChange={(event) => patch({ max_total_mg: event.target.value })}
              />
              <span className="dose-hint">{t("pricing.dosing.maxTotalHint")}</span>
            </Field>
            <Field label={t("pricing.dosing.frequency")}>
              <Combobox
                value={form.frequency_minutes}
                onChange={(value) => patch({ frequency_minutes: value })}
                emptyLabel={t("pricing.dosing.noFrequency")}
                options={FREQUENCIES.map((minutes) => ({
                  value: String(minutes),
                  label:
                    minutes % 60 === 0
                      ? t("sheet.frequency", { hours: minutes / 60 })
                      : t("sheet.frequencyMinutes", { minutes }),
                }))}
              />
            </Field>
          </div>

          <Field label={t("pricing.dosing.warning")}>
            <input
              style={inputStyle}
              value={form.warning}
              placeholder={t("pricing.dosing.warningPlaceholder")}
              onChange={(event) => patch({ warning: event.target.value })}
            />
          </Field>

          <Field label={t("pricing.dosing.breeds")}>
            <input
              style={inputStyle}
              value={form.breeds}
              placeholder={t("pricing.dosing.breedsPlaceholder")}
              onChange={(event) => patch({ breeds: event.target.value })}
            />
            <span className="dose-hint">{t("pricing.dosing.breedsHint")}</span>
          </Field>

          {/* O aviso da raça só faz sentido depois de existir uma raça. */}
          {form.breeds.trim() ? (
            <Field label={t("pricing.dosing.breedWarning")}>
              <input
                style={inputStyle}
                value={form.breed_warning}
                placeholder={t("pricing.dosing.breedWarningPlaceholder")}
                onChange={(event) => patch({ breed_warning: event.target.value })}
              />
            </Field>
          ) : null}
        </>
      )}

      <Field label={t("pricing.dosing.source")}>
        <input
          style={inputStyle}
          value={form.source}
          placeholder={t("pricing.dosing.sourcePlaceholder")}
          onChange={(event) => patch({ source: event.target.value })}
        />
      </Field>

      {/* Conferir uma dose é ato de quem tem registro no conselho: o técnico e
          o administrador cadastram a tabela, mas não assinam a posologia. Quem
          não pode não vê a caixa, e sim por que a regra não vai preencher. */}
      {canReview ? (
        <CheckRow
          checked={form.reviewed}
          onChange={(value) => patch({ reviewed: value })}
          label={t("pricing.dosing.review")}
          hint={t("pricing.dosing.reviewHint")}
        />
      ) : (
        <AdminNote tone="neutral">{t("pricing.dosing.reviewNeedsVet")}</AdminNote>
      )}

      <div className="admin-toolbar">
        <Button disabled={!valid || busy} onClick={onSave}>
          {t("pricing.dosing.save")}
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          {t("common.cancel")}
        </Button>
        {form.existing ? (
          <Button variant="secondary" disabled={busy} onClick={onRemove}>
            {t("pricing.dosing.remove")}
          </Button>
        ) : null}
      </div>
    </section>
  );
}
