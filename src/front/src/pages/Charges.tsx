import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { CAN } from "../api/capabilities";
import { ApiError, api, asList } from "../api/client";
import type { ChargeDay, ChargeItem, PriceListItem, Statement } from "../api/types";
import { Combobox, type ComboboxOption } from "../components/Combobox";
import { PinDialog } from "../components/PinDialog";
import { Gate } from "../components/authz";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  ErrorState,
  Field,
  Section,
  Skeleton,
  Stat,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { usePatientContext } from "./Patient";
import "../styles/billing.css";


/** O extrato agrupa por dia local da clínica (`date`, no formato YYYY-MM-DD). */
function dayKey(day: ChargeDay): string {
  return day.date;
}

function quantityOf(item: ChargeItem): number {
  const value = Number(item.quantity);
  return Number.isFinite(value) ? value : 1;
}

/** Centavos a partir de um campo digitado ("45", "45,50", "45.50"). */
function minorFromInput(raw: string): number | null {
  const normalized = raw.trim().replace(/\s/g, "").replace(",", ".");
  if (!normalized) return null;
  const value = Number(normalized);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

function csvCell(value: string): string {
  return `"${value.replace(/"/g, '""')}"`;
}

/** Conta da internação.
 *
 *  É uma aba do paciente: identificação, box, dias e vet responsável são do
 *  cabeçalho de `Patient.tsx`. Aqui fica o dinheiro. */
export function Charges() {
  const { detail } = usePatientContext();
  const { t, i18n } = useTranslation();
  const { money, time, day, todayKey, currency } = useClinic();
  const describeError = useApiErrorMessage();
  const hospitalizationId = detail.hospitalization.id;

  const [statement, setStatement] = useState<Statement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualOpen, setManualOpen] = useState(false);
  const [askPin, setAskPin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [priceItems, setPriceItems] = useState<PriceListItem[] | null>(null);
  const [priceError, setPriceError] = useState<string | null>(null);
  const [priceItemId, setPriceItemId] = useState("");
  const [description, setDescription] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [unitPrice, setUnitPrice] = useState("");

  const load = useCallback(async () => {
    try {
      setStatement(await api.statement(hospitalizationId));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [hospitalizationId, describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  // Quantidade não é dinheiro nem hora: só o separador decimal do idioma. O
  // relógio e a moeda vêm da clínica (useClinic), nunca do aparelho.
  const quantityFmt = useMemo(
    () => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2 }),
    [i18n.language],
  );

  const days = useMemo(
    () => [...(statement?.days ?? [])].sort((a, b) => dayKey(b).localeCompare(dayKey(a))),
    [statement],
  );

  // "Hoje" pelo dia da CLÍNICA. O extrato é agrupado no fuso da clínica
  // (`ChargeService.statement`) e o card comparava com o dia do NAVEGADOR: numa
  // estação fora do fuso o cartão zerava (R$ 0,00 num dia com lançamentos).
  const today = days.find((row) => dayKey(row) === todayKey()) ?? null;

  /** Executado e não cobrado NA JANELA da ficha.
   *
   *  Era anunciado como número da internação inteira, mas nasce de
   *  `detail.tasks` (±12h). Numa internação de cinco dias ele dizia "2" quando
   *  havia dez: um número de dinheiro silenciosamente errado é pior que número
   *  nenhum, então ele passa a declarar o período que cobre. */

  const openManual = useCallback(() => {
    setManualOpen(true);
    if (priceItems !== null) return;
    // Digitar descrição e centavos à mão é justamente o que a tabela de preços
    // existe para evitar, e o erro de digitação vai direto para a conta do
    // tutor. A lista só é buscada quando o formulário abre.
    void api
      .priceList()
      .then((page) => {
        setPriceItems(asList(page));
        setPriceError(null);
      })
      .catch((err) => {
        // Sem a tabela ainda dá para lançar à mão: o formulário continua, mas a
        // pessoa precisa saber por que a lista está vazia.
        setPriceItems([]);
        setPriceError(describeError(err));
      });
  }, [priceItems, describeError]);

  const chooseItem = useCallback(
    (value: string) => {
      setPriceItemId(value);
      const item = (priceItems ?? []).find((row) => row.id === value);
      // Sem item escolhido o que já foi digitado continua valendo: o campo
      // livre é a saída para o que não está no catálogo.
      if (!item) return;
      setDescription(item.name);
      setUnitPrice((item.price_minor / 100).toFixed(2));
    },
    [priceItems],
  );

  const priceOptions = useMemo<ComboboxOption[]>(
    () =>
      (priceItems ?? []).map((item) => ({
        value: item.id,
        label: item.name,
        hint: [money(item.price_minor), item.unit, item.is_daily_rate ? t("charges.source.daily_rate") : null]
          .filter(Boolean)
          .join(" · "),
        keywords: [item.code, t(`prescription.category.${item.category}`)].filter(Boolean).join(" "),
      })),
    [priceItems, money, t],
  );

  const closeManual = useCallback(() => {
    setManualOpen(false);
    setPriceItemId("");
    setDescription("");
    setQuantity("1");
    setUnitPrice("");
  }, []);

  const submitManual = useCallback(async () => {
    const priceMinor = minorFromInput(unitPrice);
    const quantityValue = Number(quantity.replace(",", "."));
    if (!description.trim() || priceMinor === null || !(quantityValue > 0)) {
      setError(t("charges.manual.invalid"));
      return;
    }
    setBusy(true);
    try {
      await api.addManualCharge(hospitalizationId, {
        // O item do catálogo vai junto para o lançamento saber de onde veio; o
        // servidor COPIA descrição e preço, então o que está na tela é o que
        // entra na conta mesmo depois de um reajuste da tabela.
        price_list_item_id: priceItemId || undefined,
        description: description.trim(),
        quantity: quantityValue,
        unit_price_minor: priceMinor,
      });
      closeManual();
      setError(null);
      await load();
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [
    hospitalizationId,
    priceItemId,
    description,
    quantity,
    unitPrice,
    closeManual,
    load,
    describeError,
    t,
  ]);

  const exportCsv = useCallback(() => {
    const header = [
      t("charges.csv.day"),
      t("charges.col.time"),
      t("charges.col.item"),
      t("charges.col.source"),
      t("charges.col.quantity"),
      t("charges.csv.unitPrice"),
      t("charges.col.amount"),
    ];
    const lines = [header.map(csvCell).join(",")];
    for (const row of [...days].reverse()) {
      for (const item of row.items) {
        lines.push(
          [
            dayKey(row),
            time(item.charged_at),
            item.description,
            t(`charges.source.${item.source}`),
            String(quantityOf(item)),
            (item.unit_price_minor / 100).toFixed(2),
            (item.total_minor / 100).toFixed(2),
          ]
            .map(csvCell)
            .join(","),
        );
      }
    }
    // BOM: sem ele o Excel abre acento quebrado.
    const blob = new Blob([`﻿${lines.join("\r\n")}\r\n`], {
      type: "text/csv;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `conta-${hospitalizationId}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  }, [days, hospitalizationId, t, time]);

  // Extrato que não veio não é extrato zerado: um "R$ 0,00" aqui manda a
  // clínica cobrar errado.
  if (error && !statement) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }

  return (
    <>
      <ErrorBanner message={error} />

      <Section
        title={t("charges.title")}
        hint={t("charges.subtitle")}
        actions={
          <>
            <Button variant="secondary" onClick={exportCsv} disabled={!statement}>
              {t("charges.exportCsv")}
            </Button>
            {/* Lançar item é ESCRITA: a capacidade foi separada da leitura no
                servidor, e o botão segue a separação. */}
            <Gate can={CAN.chargesWrite}>
              <Button onClick={openManual}>{t("charges.addManual")}</Button>
            </Gate>
          </>
        }
      >
        {!statement ? (
          <Skeleton rows={1} height={92} />
        ) : (
          <div className="charges-stats">
            <Stat value={money(statement.total_minor)} label={t("charges.total")} />
            <Stat
              value={money(today?.total_minor ?? 0)}
              label={t("charges.today")}
              hint={t("charges.todayEntries", { entries: today?.items.length ?? 0 })}
            />
          </div>
        )}
      </Section>

      {statement && days.length === 0 ? (
        <EmptyState title={t("charges.empty")} hint={t("charges.emptyHint")} />
      ) : null}

      {days.map((row) => {
        const key = dayKey(row);
        // O rótulo do dia sai do instante de um lançamento dele, formatado no
        // fuso da clínica, o mesmo fuso em que o servidor agrupou. Remontar a
        // data civil "2026-08-30" no navegador trocava o dia por um.
        const label = day(row.items[0]?.charged_at ?? new Date());
        return (
          <Section
            key={key}
            title={key === todayKey() ? `${t("charges.today.label")} · ${label}` : label}
          >
            <Card style={{ padding: 0, overflow: "hidden" }}>
              <div className="charges-scroll">
                <table className="charges-table">
                  <thead>
                    <tr>
                      <th>{t("charges.col.time")}</th>
                      <th>{t("charges.col.item")}</th>
                      <th>{t("charges.col.source")}</th>
                      <th className="num">{t("charges.col.quantity")}</th>
                      <th className="num">{t("charges.col.amount")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {row.items.map((item) => (
                      <tr key={item.id}>
                        <td className="tabular" style={{ color: "var(--ink-2)" }}>
                          {time(item.charged_at)}
                        </td>
                        <td>{item.description}</td>
                        <td>
                          <span className="charges-source">
                            {t(`charges.source.${item.source}`)}
                          </span>
                        </td>
                        <td className="num tabular" style={{ color: "var(--ink-2)" }}>
                          {quantityFmt.format(quantityOf(item))}
                        </td>
                        <td className="num tabular" style={{ fontWeight: 600 }}>
                          {money(item.total_minor)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="charges-day-total">
                <span style={{ fontSize: 13.5, color: "var(--ink-2)" }}>
                  {t("charges.dayTotal", { entries: row.items.length })}
                </span>
                <strong
                  className="tabular"
                  style={{ fontFamily: "'Bricolage Grotesque', system-ui", fontSize: 20 }}
                >
                  {money(row.total_minor)}
                </strong>
              </div>
            </Card>
          </Section>
        );
      })}

      <p className="billing-footnote">{t("charges.footer")}</p>

      {manualOpen && !askPin ? (
        <div className="modal-backdrop" role="dialog" aria-modal="true">
          <div className="modal-card">
            <h2 style={{ fontSize: 20 }}>{t("charges.manual.title")}</h2>

            <Field label={t("charges.manual.fromPriceList")}>
              <Combobox
                value={priceItemId}
                onChange={chooseItem}
                options={priceOptions}
                emptyLabel={t("charges.manual.freeText")}
                placeholder={t("charges.manual.pickHint")}
                disabled={priceItems === null}
              />
            </Field>

            {priceError ? (
              <p style={{ margin: 0, fontSize: 12.5, color: "var(--late)" }}>
                {t("charges.manual.priceListFailed", { reason: priceError })}
              </p>
            ) : null}

            <Field label={t("charges.manual.description")}>
              <input
                style={inputStyle}
                value={description}
                onChange={(event) => {
                  setDescription(event.target.value);
                  // Descrição editada à mão deixa de ser a do catálogo: o
                  // lançamento não pode alegar uma origem que não é a dele.
                  setPriceItemId("");
                }}
              />
            </Field>

            <div className="form-grid-2">
              <Field label={t("charges.manual.quantity")}>
                <input
                  style={inputStyle}
                  inputMode="decimal"
                  value={quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                />
              </Field>
              <Field label={t("charges.manual.unitPrice", { currency })}>
                <input
                  style={inputStyle}
                  inputMode="decimal"
                  value={unitPrice}
                  onChange={(event) => setUnitPrice(event.target.value)}
                  placeholder={t("charges.manual.unitPriceHint")}
                />
              </Field>
            </div>

            <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>
              {t("charges.manual.hint")}
            </p>

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <Button variant="secondary" onClick={closeManual} disabled={busy}>
                {t("common.cancel")}
              </Button>
              <Button onClick={() => void submitManual()} disabled={busy}>
                {t("charges.manual.submit")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}

      {askPin ? (
        <PinDialog
          context={t("charges.manual.title")}
          onDone={() => {
            setAskPin(false);
            void submitManual();
          }}
          onCancel={() => setAskPin(false)}
        />
      ) : null}
    </>
  );
}
