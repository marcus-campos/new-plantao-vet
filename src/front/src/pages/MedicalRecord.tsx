import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { CAN } from "../api/capabilities";
import { api } from "../api/client";
import type { MedicalRecord as MedicalRecordData, Prescription, ProgressNote } from "../api/types";
import { Gate } from "../components/authz";
import {
  Button,
  Card,
  ErrorBanner,
  ErrorState,
  Section,
  Skeleton,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { useSession } from "../hooks/useSession";
import { usePatientContext } from "./Patient";
import "../styles/billing.css";

/** Seções do documento. A chave é o que o servidor entende em `include`
 *  (`ALLOWED_SECTIONS`), e `capability` é o que ele exige para devolvê-la: a
 *  rota responde 403 na SEÇÃO de conta para quem não tem `charges.read`, então
 *  pedir escondido derrubaria o prontuário inteiro. */
const SECTIONS = [
  { key: "progress_notes", label: "record.section.progressNotes", capability: null },
  { key: "tasks", label: "record.section.executions", capability: null },
  { key: "prescriptions", label: "record.section.prescriptions", capability: null },
  { key: "charges", label: "record.section.charges", capability: CAN.chargesRead },
] as const;

type SectionKey = (typeof SECTIONS)[number]["key"];

/** O default do servidor: a conta NÃO entra na cópia do tutor sem se pedir. */
const DEFAULT_SECTIONS: SectionKey[] = ["progress_notes", "tasks", "prescriptions"];

/** O PDF não passa pelo `request` do cliente: aquele caminho devolve JSON, e
 *  aqui o corpo é binário. A base da API é a mesma; enquanto `client.ts` não
 *  a exportar, ela é lida do mesmo env var, no mesmo default. */

type Unknowns = Record<string, unknown>;

function asObject(value: unknown): Unknowns {
  return typeof value === "object" && value !== null ? (value as Unknowns) : {};
}

function asText(value: unknown): string | null {
  if (typeof value === "number") return String(value);
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

function asMinor(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** Nome + registro do conselho: o perfil brasileiro exige os dois em cada ato. */
function signature(
  name: string | null,
  number: string | null,
  authority: string | null,
): string | null {
  if (!name) return null;
  const license = [authority, number].filter(Boolean).join(" ");
  return license ? `${name} · ${license}` : name;
}

/** Prontuário da internação: o documento que sai da tela para o papel.
 *
 *  A identificação do paciente aparece DENTRO da folha porque ela é parte do
 *  documento; a tela não repete o cabeçalho do paciente, que é de `Patient.tsx`.
 */
export function MedicalRecord() {
  const { detail } = usePatientContext();
  const { t } = useTranslation();
  const { day, time, moment, money } = useClinic();
  const { can } = useSession();
  const describeError = useApiErrorMessage();
  const hospitalizationId = detail.hospitalization.id;

  const [record, setRecord] = useState<MedicalRecordData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sections, setSections] = useState<SectionKey[]>(DEFAULT_SECTIONS);
  const [downloading, setDownloading] = useState(false);

  // O que se pode PEDIR agora. Numa estação o operator token expira em 5 min:
  // continuar pedindo a seção de conta depois disso devolveria 403 e derrubaria
  // o prontuário inteiro por causa de uma seção acessória.
  const allowed = useMemo(
    () =>
      sections.filter((key) => {
        const capability = SECTIONS.find((section) => section.key === key)?.capability;
        return !capability || can(capability);
      }),
    [sections, can],
  );
  const include = allowed.join(",");

  const load = useCallback(async () => {
    try {
      setRecord(await api.medicalRecord(hospitalizationId, include));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [hospitalizationId, include, describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = useCallback((key: SectionKey) => {
    setSections((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }, []);

  /** Baixa o PDF que o SERVIDOR gera.
   *
   *  O botão dizia "Baixar PDF" e chamava `window.print()`: o tutor recebia o
   *  que a impressora do navegador quisesse, sem timbre da clínica, sem
   *  paginação e limitado às seções que a tela tinha carregado. Agora o
   *  arquivo vem pronto de `/record.pdf`, com as MESMAS seções pedidas aqui. */
  const download = useCallback(async () => {
    setDownloading(true);
    try {
      const href = URL.createObjectURL(await api.medicalRecordPdf(hospitalizationId, include));
      const anchor = document.createElement("a");
      anchor.href = href;
      // Mesmo nome que o servidor manda no Content-Disposition.
      anchor.download = `record-${hospitalizationId}.pdf`;
      anchor.click();
      URL.revokeObjectURL(href);
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setDownloading(false);
    }
  }, [hospitalizationId, include, describeError]);

  /** Carimbo do documento: dia E hora do relógio da clínica.
   *
   *  `moment()` esconde a data quando o registro é de hoje. Na tela isso é
   *  bom; num papel que sai da clínica "18:05" sozinho não diz nada. */
  const stamp = useCallback((iso: string) => `${day(iso)} · ${time(iso)}`, [day, time]);

  if (error && !record) {
    return <ErrorState message={error} onRetry={() => void load()} />;
  }
  if (!record) return <Skeleton rows={4} />;

  const patient = record.patient;
  const patientLine = [
    patient?.species,
    patient?.breed,
    patient?.weight_kg ? `${patient.weight_kg} kg` : null,
  ]
    .filter(Boolean)
    .join(", ");

  const hospitalization = record.hospitalization;
  const period = `${day(hospitalization.admitted_at)} – ${
    hospitalization.ended_at ? day(hospitalization.ended_at) : t("record.ongoing")
  }`;

  const vetSignature =
    signature(
      record.vet?.name ?? null,
      record.vet?.license_number ?? null,
      record.vet?.license_authority ?? null,
    ) ?? "–";

  const notes: ProgressNote[] = record.progress_notes ?? [];
  const executions: Unknowns[] = record.tasks ?? [];
  const prescriptions: Prescription[] = record.prescriptions ?? [];
  const charges: Unknowns[] = record.charges ?? [];
  const chargesTotal = charges.reduce((sum, row) => sum + asMinor(row.total_minor), 0);

  return (
    <>
      <div className="no-print" style={{ display: "grid", gap: 14 }}>
        <ErrorBanner message={error} />

        <Section
          title={t("record.title")}
          hint={t("record.screenNote")}
          actions={
            <div style={{ display: "flex", gap: 10 }}>
              {/* Imprimir continua aqui como ação secundária: quem quer o papel
                  na hora não precisa baixar antes. */}
              <Button variant="secondary" onClick={() => window.print()}>
                {t("record.print")}
              </Button>
              <Button onClick={() => void download()} disabled={downloading}>
                {t("record.download")}
              </Button>
            </div>
          }
        >
          <Card style={{ padding: "10px 16px" }}>
            <div className="record-toolbar">
              <span className="billing-eyebrow">{t("record.include")}</span>
              {SECTIONS.map((section) => {
                const check = (
                  <label key={section.key} className="record-check">
                    <input
                      type="checkbox"
                      checked={sections.includes(section.key)}
                      onChange={() => toggle(section.key)}
                    />
                    {t(section.label)}
                  </label>
                );
                return section.capability ? (
                  <Gate key={section.key} can={section.capability}>
                    {check}
                  </Gate>
                ) : (
                  check
                );
              })}
              <span style={{ marginLeft: "auto", fontSize: 12.5, color: "var(--ink-3)" }}>
                {/* `record.printHint` dizia que o PDF sai pela impressão do
                    navegador. Deixou de ser verdade quando o servidor passou a
                    gerar o arquivo. */}
                {t("record.previewHint", { defaultValue: "pré-visualização do documento" })}
              </span>
            </div>
          </Card>
        </Section>
      </div>

      <div className="record-stage">
        <article className="record-sheet">
          <header className="record-letterhead">
            {/* A API devolve o NOME da clínica e nada mais (`RecordOut`).
                Endereço, telefone e CNPJ eram lidos de campos que não existem:
                o timbre fica com o que é verdade. */}
            <div style={{ display: "grid", gap: 3 }}>
              <div
                style={{
                  fontFamily: "'Bricolage Grotesque', system-ui",
                  fontWeight: 800,
                  fontSize: 20,
                }}
              >
                {record.clinic_name}
              </div>
            </div>
            <div style={{ display: "grid", gap: 3, textAlign: "right" }}>
              <div className="record-label">{t("record.documentTitle")}</div>
              {/* Era `id.slice(0, 8)` sob o rótulo "Internação nº": um número
                  legal inventado num documento regulado. É o id interno, e
                  aparece dito com todas as letras. */}
              <div className="tabular" style={{ fontSize: 11, color: "var(--ink-3)" }}>
                {t("record.internalId", { id: hospitalization.id })}
              </div>
            </div>
          </header>

          <section className="record-identity">
            <div style={{ display: "grid", gap: 2 }}>
              <div className="record-label">{t("record.patient")}</div>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                {[patient?.name, patientLine].filter(Boolean).join(" · ") || "–"}
              </div>
              {record.owner_name ? (
                <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
                  {t("record.owner")}: {record.owner_name}
                </div>
              ) : null}
            </div>

            <div style={{ display: "grid", gap: 2 }}>
              <div className="record-label">{t("record.vet")}</div>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{vetSignature}</div>
            </div>

            <div style={{ display: "grid", gap: 2 }}>
              <div className="record-label">{t("record.period")}</div>
              <div className="tabular" style={{ fontSize: 13.5, fontWeight: 600 }}>
                {period}
              </div>
              <div style={{ fontSize: 12.5, color: "var(--ink-2)" }}>
                {t(`record.status.${hospitalization.status}`, {
                  defaultValue: hospitalization.status,
                })}
              </div>
            </div>
          </section>

          {allowed.includes("progress_notes") ? (
            <section style={{ display: "grid", gap: 8 }}>
              <h2 className="record-section-title">{t("record.notes")}</h2>
              {notes.length === 0 ? (
                <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                  {t("record.emptySection")}
                </p>
              ) : (
                notes.map((note) => (
                  <div key={note.id} className="record-note" style={{ display: "grid", gap: 4 }}>
                    <div className="record-note-head">
                      <strong className="tabular">{stamp(note.signed_at)}</strong>
                      <span style={{ color: "var(--ink-2)", fontSize: 12 }}>
                        {t("record.signedBy", {
                          name:
                            signature(
                              note.author_name,
                              note.author_license,
                              note.author_license_authority,
                            ) ?? note.author_name,
                        })}
                        {note.amends_progress_note_id ? ` · ${t("record.amendment")}` : ""}
                      </span>
                    </div>
                    <NoteBody note={note} />
                  </div>
                ))
              )}
            </section>
          ) : null}

          {allowed.includes("tasks") ? (
            <section style={{ display: "grid", gap: 8 }}>
              <h2 className="record-section-title">{t("record.executions")}</h2>
              {executions.length === 0 ? (
                <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                  {t("record.emptySection")}
                </p>
              ) : (
                <>
                  <div className="record-exec record-exec-head record-label">
                    <div>{t("record.col.time")}</div>
                    <div>{t("record.col.item")}</div>
                    <div>{t("record.col.by")}</div>
                  </div>
                  {executions.map((execution, index) => {
                    const executedAt = asText(execution.executed_at);
                    const author = asObject(execution.author);
                    const state = asText(execution.status);
                    const reason = asText(execution.outcome_reason);
                    return (
                      <div key={asText(execution.id) ?? index} className="record-exec">
                        <div className="tabular" style={{ color: "var(--ink-2)" }}>
                          {executedAt ? moment(executedAt) : "–"}
                        </div>
                        <div>
                          {asText(execution.title) ?? "–"}
                          {state && state !== "done" ? (
                            <span style={{ color: "var(--ink-3)" }}>
                              {" · "}
                              {t(`state.${state}`, { defaultValue: t("record.notExecuted") })}
                              {reason
                                ? ` · ${t(`task.reason.${reason}`, { defaultValue: reason })}`
                                : ""}
                            </span>
                          ) : null}
                        </div>
                        <div style={{ color: "var(--ink-2)" }}>
                          {signature(
                            asText(author.name),
                            asText(author.license_number),
                            asText(author.license_authority),
                          ) ?? "–"}
                        </div>
                      </div>
                    );
                  })}
                </>
              )}
            </section>
          ) : null}

          {allowed.includes("prescriptions") ? (
            <section style={{ display: "grid", gap: 8 }}>
              <h2 className="record-section-title">{t("record.prescriptions")}</h2>
              {prescriptions.length === 0 ? (
                <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                  {t("record.emptySection")}
                </p>
              ) : (
                prescriptions.map((prescription) => (
                  <div key={prescription.id} className="record-exec">
                    <div className="tabular" style={{ color: "var(--ink-2)" }}>
                      {moment(prescription.starts_at)}
                    </div>
                    <div>{prescription.name}</div>
                    <div style={{ color: "var(--ink-2)" }}>
                      {t(`prescription.category.${prescription.category}`)}
                    </div>
                  </div>
                ))
              )}
            </section>
          ) : null}

          {/* A conta é seção da API e faltava no seletor. Quem precisava dela
              exportava o CSV por fora e grampeava no documento. Só entra com a
              capacidade de leitura da conta, a mesma que a rota exige. */}
          {allowed.includes("charges") ? (
            <section style={{ display: "grid", gap: 8 }}>
              <h2 className="record-section-title">{t("record.charges")}</h2>
              {charges.length === 0 ? (
                <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
                  {t("record.emptySection")}
                </p>
              ) : (
                <>
                  <div className="record-exec record-exec-head record-label">
                    <div>{t("charges.col.quantity")}</div>
                    <div>{t("record.col.item")}</div>
                    <div>{t("charges.col.amount")}</div>
                  </div>
                  {charges.map((charge, index) => (
                    <div key={asText(charge.id) ?? index} className="record-exec">
                      <div className="tabular" style={{ color: "var(--ink-2)" }}>
                        {asText(charge.quantity) ?? "–"}
                      </div>
                      <div>{asText(charge.description) ?? "–"}</div>
                      <div className="tabular">{money(asMinor(charge.total_minor))}</div>
                    </div>
                  ))}
                  <div className="record-exec">
                    <div />
                    <div style={{ fontWeight: 700 }}>{t("record.chargesTotal")}</div>
                    <div className="tabular" style={{ fontWeight: 700 }}>
                      {money(chargesTotal)}
                    </div>
                  </div>
                </>
              )}
            </section>
          ) : null}

          {allowed.length === 0 ? (
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
              {t("record.emptyDocument")}
            </p>
          ) : null}

          <footer className="record-legal">
            <span className="tabular">
              {t("record.generatedAt", { when: stamp(record.generated_at) })}
            </span>
            <span>{t("record.legal")}</span>
          </footer>
        </article>
      </div>

      <p className="billing-footnote no-print">{t("record.footer")}</p>
    </>
  );
}

function NoteBody({ note }: { note: ProgressNote }) {
  const { t } = useTranslation();
  const parts: [string, string | null][] = [
    ["record.note.subjective", note.subjective],
    ["record.note.findings", note.findings],
    ["record.note.assessment", note.assessment],
    ["record.note.plan", note.plan],
  ];
  const filled = parts.filter(([, value]) => value && value.trim() !== "");
  if (filled.length === 0) {
    return (
      <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>{t("record.emptySection")}</p>
    );
  }
  return (
    <div style={{ display: "grid", gap: 3, fontSize: 13, lineHeight: 1.55 }}>
      {filled.map(([label, value]) => (
        <p key={label} style={{ margin: 0 }}>
          <strong style={{ color: "var(--ink-2)" }}>{t(label)}: </strong>
          {value}
        </p>
      ))}
    </div>
  );
}
