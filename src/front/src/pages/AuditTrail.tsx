import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { AuditEntry } from "../api/types";
import { AdminNote } from "../components/AdminShared";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Section,
  Skeleton,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import "../styles/admin.css";

const PAGE_SIZE = 50;

/** Tipos de entidade que a trilha registra (services/audit.py). */
const ENTITY_TYPES = [
  "task",
  "prescription",
  "hospitalization",
  "progress_note",
  "handover_report",
  "shift",
  "shift_note",
  "patient",
  "owner",
  "owner_contact",
  "kennel",
  "price_list_item",
  "charge_item",
  "membership",
  "clinic",
  "station",
];

/** A API tipa `entity_id` como UUID: mandar qualquer outra coisa volta 422 e a
 *  tela mostraria "algo deu errado" para um erro de digitação. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "–";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export function AuditTrail() {
  const { t } = useTranslation();
  const clinic = useClinic();
  // A auditoria é só leitura: nada aqui muda estado, então não há PIN a pedir.
  const describeError = useApiErrorMessage();
  const [params, setParams] = useSearchParams();
  const [error, setError] = useState<string | null>(null);

  /* O filtro vive na URL. A pergunta que se faz a uma trilha nunca é "o que
     aconteceu na clínica", é "o que aconteceu COM ESTE registro": esta
     prescrição, esta internação, este box. Com o filtro na query string, as
     outras telas conseguem apontar para cá já com a pergunta feita
     (`/gestao/auditoria?entity_type=prescription&entity_id=…`). */
  const entityType = params.get("entity_type") || "all";
  const entityId = params.get("entity_id") ?? "";

  const [idDraft, setIdDraft] = useState(entityId);
  const [entries, setEntries] = useState<AuditEntry[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<AuditEntry | null>(null);

  // A URL manda: voltar no histórico do navegador tem de recolocar o filtro
  // na caixa, não só na lista.
  useEffect(() => {
    setIdDraft(entityId);
  }, [entityId]);

  const idIsValid = entityId === "" || UUID.test(entityId);

  const fetchPage = useCallback(
    async (from: string | null, append: boolean) => {
      if (!idIsValid) return;
      if (append) setLoadingMore(true);
      else setEntries(null);
      try {
        const page = await api.audit({
          entity_type: entityType === "all" ? undefined : entityType,
          entity_id: entityId || undefined,
          cursor: from ?? undefined,
          limit: PAGE_SIZE,
        });
        setEntries((current) => (append ? [...(current ?? []), ...page.items] : page.items));
        setCursor(page.next_cursor);
        // Trocar de filtro recomeça a lista: o painel aberto não pertence mais a ela.
        if (!append) setSelected(null);
        setError(null);
      } catch (err) {
        // Trilha que falha e aparece vazia é a pior mentira possível numa tela
        // de conformidade: "não há registro" e "não consegui ler" não são a
        // mesma frase.
        if (!append) setEntries(null);
        setError(describeError(err));
      } finally {
        setLoadingMore(false);
      }
    },
    [entityType, entityId, idIsValid, describeError],
  );

  useEffect(() => {
    void fetchPage(null, false);
  }, [fetchPage]);

  function setFilter(next: { entity_type?: string; entity_id?: string }) {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (!value || value === "all") merged.delete(key);
      else merged.set(key, value);
    }
    setParams(merged, { replace: true });
  }

  function actionLabel(action: string): string {
    // Ação sem tradução aparece com o código cru: melhor um código honesto
    // do que uma frase inventada sobre o que aconteceu.
    return t(`audit.action.${action}`, { defaultValue: action });
  }

  function entityLabel(type: string): string {
    return t(`audit.entity.${type}`, { defaultValue: type });
  }

  const rows = entries ?? [];

  return (
    <>
      <Section
        title={t("audit.title")}
        hint={t("audit.subtitle")}
        actions={
          entityType !== "all" || entityId ? (
            <Button variant="secondary" onClick={() => setFilter({ entity_type: "", entity_id: "" })}>
              {t("audit.clearFilter")}
            </Button>
          ) : null
        }
      >
        <div className="chip-group">
          {["all", ...ENTITY_TYPES].map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={entityType === option}
              className={entityType === option ? "chip chip-on" : "chip"}
              onClick={() => setFilter({ entity_type: option })}
            >
              {option === "all" ? t("audit.filter.all") : entityLabel(option)}
            </button>
          ))}
        </div>

        <Card style={{ display: "grid", gap: 8 }}>
          <form
            className="admin-toolbar"
            onSubmit={(event) => {
              event.preventDefault();
              setFilter({ entity_id: idDraft.trim() });
            }}
          >
            <input
              style={inputStyle}
              className="tabular"
              value={idDraft}
              placeholder={t("audit.entityIdPlaceholder")}
              aria-label={t("audit.entityIdLabel")}
              onChange={(event) => setIdDraft(event.target.value)}
            />
            <Button type="submit" variant="secondary">
              {t("audit.entityIdApply")}
            </Button>
          </form>
          <p className="admin-footnote">{t("audit.entityIdHint")}</p>
          {!idIsValid ? <AdminNote tone="danger">{t("audit.entityIdInvalid")}</AdminNote> : null}
        </Card>

        {error ? <ErrorState message={error} onRetry={() => void fetchPage(null, false)} /> : null}
        {!error && idIsValid && entries === null ? <Skeleton rows={6} height={44} /> : null}

        {!error && idIsValid && entries !== null ? (
          <div className="audit-layout">
            <Card style={{ display: "grid", gap: 12 }}>
              {rows.length === 0 ? (
                <EmptyState
                  title={t("audit.empty")}
                  hint={
                    entityType !== "all" || entityId
                      ? t("audit.emptyFilterHint")
                      : t("audit.emptyHint")
                  }
                />
              ) : (
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>{t("audit.col.time")}</th>
                        <th>{t("audit.col.action")}</th>
                        <th>{t("audit.col.who")}</th>
                        <th>{t("audit.col.entity")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((entry) => (
                        <tr
                          key={entry.id}
                          className={
                            selected?.id === entry.id
                              ? "admin-row-click admin-row-on"
                              : "admin-row-click"
                          }
                          tabIndex={0}
                          onClick={() => setSelected(entry)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") setSelected(entry);
                          }}
                        >
                          <td
                            className="tabular admin-cell-muted"
                            style={{ whiteSpace: "nowrap" }}
                          >
                            {/* Hora do relógio da CLÍNICA: a trilha é documento
                                de conformidade e é lida em quiosque, celular e
                                notebook, cada um num fuso. */}
                            {clinic.moment(entry.created_at)}
                          </td>
                          <td style={{ fontWeight: 600 }}>{actionLabel(entry.action)}</td>
                          <td>
                            <span style={{ display: "block" }}>{entry.actor_name}</span>
                            {entry.actor_license ? (
                              <span className="admin-cell-muted" style={{ fontSize: 12.5 }}>
                                {entry.actor_license_authority
                                  ? `${entry.actor_license_authority} ${entry.actor_license}`
                                  : entry.actor_license}
                              </span>
                            ) : null}
                          </td>
                          <td className="admin-cell-muted">{entityLabel(entry.entity_type)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <div className="admin-toolbar">
                {cursor ? (
                  <Button
                    variant="secondary"
                    disabled={loadingMore}
                    onClick={() => void fetchPage(cursor, true)}
                  >
                    {loadingMore ? t("common.loading") : t("audit.loadMore")}
                  </Button>
                ) : rows.length > 0 ? (
                  <span className="admin-footnote">
                    {t("audit.allLoaded", { total: rows.length })}
                  </span>
                ) : null}
              </div>
            </Card>

            <div className="audit-panel">
              {selected ? (
                <EntryDetail
                  entry={selected}
                  when={`${clinic.day(selected.created_at)} · ${clinic.time(selected.created_at)}`}
                  action={actionLabel(selected.action)}
                  entity={entityLabel(selected.entity_type)}
                  onTrackEntity={() =>
                    setFilter({
                      entity_type: selected.entity_type,
                      entity_id: selected.entity_id ?? "",
                    })
                  }
                />
              ) : (
                <Card>
                  <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-3)" }}>
                    {t("audit.selectHint")}
                  </p>
                </Card>
              )}
            </div>
          </div>
        ) : null}

        <p className="admin-footnote">{t("audit.footer")}</p>
      </Section>
    </>
  );
}

function EntryDetail({
  entry,
  when,
  action,
  entity,
  onTrackEntity,
}: {
  entry: AuditEntry;
  when: string;
  action: string;
  entity: string;
  onTrackEntity: () => void;
}) {
  const { t } = useTranslation();
  const before = entry.payload.before ?? null;
  const after = entry.payload.after ?? null;
  const extra = entry.payload.extra ?? null;

  const keys = useMemo(
    () =>
      Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])).sort(),
    [before, after],
  );

  return (
    <>
      <Card style={{ display: "grid", gap: 10 }}>
        <div>
          <span className="admin-section-label">{t("audit.detail")}</span>
          <div style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>{action}</div>
          <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
            {when} · {entity}
          </div>
          {/* A pergunta seguinte é sempre a mesma: "e o que MAIS aconteceu com
              este registro?". Era a única que a trilha não sabia responder. */}
          {entry.entity_id ? (
            <div style={{ display: "grid", gap: 6, marginTop: 8, justifyItems: "start" }}>
              <span className="integrity-hash" style={{ fontSize: 12 }}>
                {entry.entity_id}
              </span>
              <Button variant="secondary" onClick={onTrackEntity}>
                {t("audit.trackEntity")}
              </Button>
            </div>
          ) : null}
        </div>

        <div style={{ borderTop: "1px solid var(--line-soft)", paddingTop: 10 }}>
          <div style={{ fontSize: 14, fontWeight: 600 }}>{entry.actor_name}</div>
          <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
            {entry.actor_license
              ? entry.actor_license_authority
                ? `${entry.actor_license_authority} ${entry.actor_license}`
                : entry.actor_license
              : t("audit.noLicense")}
          </div>
        </div>

        {keys.length === 0 && !extra ? (
          <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>{t("audit.noPayload")}</p>
        ) : null}

        {keys.length > 0 ? (
          <div className="diff-grid">
            <DiffBox
              title={t("audit.before")}
              source={before}
              other={after}
              keys={keys}
              empty={t("audit.beforeEmpty")}
            />
            <DiffBox
              title={t("audit.after")}
              source={after}
              other={before}
              keys={keys}
              empty={t("audit.afterEmpty")}
            />
          </div>
        ) : null}

        {extra ? (
          <div className="diff-box">
            <span className="diff-title">{t("audit.extra")}</span>
            {Object.entries(extra).map(([key, value]) => (
              <div key={key} className="diff-line">
                <span className="diff-key">{key}: </span>
                {formatValue(value)}
              </div>
            ))}
          </div>
        ) : null}
      </Card>

      {/* O selo verde dizia: "qualquer alteração feita por fora do sistema
          quebraria a cadeia e apareceria aqui". Nada aqui verifica coisa
          alguma. `AuditEntryOut` nem devolve o `prev_hash` com que a cadeia
          seria conferida. Numa tela de conformidade, segurança encenada é pior
          do que nenhuma: quem confia nela deixa de conferir onde dá. Ficou o
          que é verdade: a impressão digital DESTE registro, para conferir
          contra o banco. */}
      <AdminNote tone="neutral">
        <span style={{ minWidth: 0 }}>
          <span className="admin-section-label">{t("audit.entryHash")}</span>
          <span
            className="integrity-hash"
            style={{ display: "block", marginTop: 4, overflowWrap: "anywhere" }}
          >
            {entry.entry_hash}
          </span>
          <span style={{ display: "block", marginTop: 6 }}>{t("audit.entryHashHint")}</span>
        </span>
      </AdminNote>
    </>
  );
}

function DiffBox({
  title,
  source,
  other,
  keys,
  empty,
}: {
  title: string;
  source: Record<string, unknown> | null;
  other: Record<string, unknown> | null;
  keys: string[];
  empty: string;
}) {
  if (!source) {
    return (
      <div className="diff-box">
        <span className="diff-title">{title}</span>
        <span className="diff-line diff-key">{empty}</span>
      </div>
    );
  }
  return (
    <div className="diff-box">
      <span className="diff-title">{title}</span>
      {keys.map((key) => {
        const value = formatValue(source[key]);
        const changed = other ? formatValue(other[key]) !== value : false;
        return (
          <div key={key} className="diff-line">
            <span className="diff-key">{key}: </span>
            <span className={changed ? "diff-changed" : undefined}>{value}</span>
          </div>
        );
      })}
    </div>
  );
}
