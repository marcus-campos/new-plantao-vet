import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import "../styles/patients.css";

import { api, asList } from "../api/client";
import { CAN } from "../api/capabilities";
import type {
  BoardRow,
  ClinicProfile,
  Hospitalization,
  Kennel,
  Owner,
  PatientSearchHit,
} from "../api/types";
import { Gate } from "../components/authz";
import { PatientsQuickCreate } from "../components/PatientsQuickCreate";
import {
  Button,
  EmptyState,
  ErrorState,
  Page,
  Section,
  Skeleton,
  useApiErrorMessage,
} from "../components/ui";
import { useBoard } from "../hooks/useBoard";
import { useClinic } from "../hooks/useClinic";

/** Internados: o censo da internação.
 *
 *  Absorve três itens da navegação antiga, que eram três leituras do MESMO
 *  `GET /board`:
 *
 *  · **Painel** desenhava as mesmas linhas noutro layout, com um contador que
 *    dizia "9 de 12 feitas" contando pendentes. Virou o modo `mural`, que é o
 *    que o glossário sempre disse que o Board era: "só uma URL em tela cheia".
 *  · **Internados** era esta lista.
 *  · **Boxes** cruzava paciente e box por igualdade de STRING do nome (dois
 *    boxes homônimos, ou renomear um box ocupado, punham o paciente no lugar
 *    errado). Virou o modo `boxes`, e o cruzamento agora é por id.
 *
 *  Ocupação é uma lente do censo, não um módulo. Quem procura um box livre está
 *  olhando para os mesmos pacientes por outro ângulo.
 */
type View = "lista" | "boxes";
type Filter = "all" | "critical" | "overdue";

const VIEWS: View[] = ["lista", "boxes"];
const FILTERS: Filter[] = ["all", "critical", "overdue"];

/** Busca sem acento e sem caixa: quem digita "nina" acha "Niña". */
function normalize(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim();
}

export function Inpatients() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const describeError = useApiErrorMessage();
  const [params, setParams] = useSearchParams();
  const { data, error, loading, reload } = useBoard();

  const view = (VIEWS.includes(params.get("vista") as View) ? params.get("vista") : "lista") as View;
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [archive, setArchive] = useState<PatientSearchHit[]>([]);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [owners, setOwners] = useState<Owner[]>([]);
  const [profile, setProfile] = useState<ClinicProfile | null>(null);
  const [registering, setRegistering] = useState(false);

  useEffect(() => {
    let alive = true;
    void api
      .clinicProfile()
      .then((value) => alive && setProfile(value))
      .catch(() => undefined);
    // A lista de tutores é para o cadastro rápido; sem `owner.read` ela não vem
    // e o formulário passa a exigir um tutor novo, o que é correto.
    void api
      .owners()
      .then((page) => alive && setOwners(asList(page)))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // Busca no cadastro com debounce: uma tecla não é uma requisição.
  useEffect(() => {
    const term = search.trim();
    if (term.length < 2) {
      setArchive([]);
      setArchiveError(null);
      return;
    }
    const timer = setTimeout(() => {
      void api
        .searchPatients(term)
        .then((hits) => {
          setArchive(hits);
          setArchiveError(null);
        })
        .catch((err) => {
          // Engolir esta falha fazia a recepção ler "nenhum resultado" e
          // CADASTRAR O PACIENTE DE NOVO: a duplicata que a busca existe para
          // evitar. Falha de busca precisa dizer que falhou.
          setArchive([]);
          setArchiveError(describeError(err));
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [search, describeError]);

  const rows = useMemo(() => data?.rows ?? [], [data]);
  const counts = useMemo(
    () => ({
      all: rows.length,
      critical: rows.filter((row) => row.critical_overdue).length,
      overdue: rows.filter((row) => row.counters.overdue > 0).length,
    }),
    [rows],
  );

  const visible = useMemo(() => {
    const term = normalize(search);
    return rows.filter((row) => {
      if (filter === "critical" && !row.critical_overdue) return false;
      if (filter === "overdue" && row.counters.overdue === 0) return false;
      return !term || normalize(row.patient_name).includes(term);
    });
  }, [rows, filter, search]);

  const offBoard = useMemo(() => {
    const shown = new Set(visible.map((row) => row.hospitalization_id));
    return archive.filter(
      (hit) => hit.active_hospitalization_id === null || !shown.has(hit.active_hospitalization_id),
    );
  }, [archive, visible]);

  const searchPlaceholder = useMemo(() => {
    const kinds = (profile?.patient_identifier_kinds ?? []).map((kind) => t(kind.label_key));
    if (kinds.length === 0) return t("patients.search.placeholder");
    return t("patients.search.placeholderWith", { kinds: kinds.join(", ") });
  }, [profile, t]);

  const identifierLabel = useCallback(
    (kind: string) => {
      const match = profile?.patient_identifier_kinds.find((item) => item.kind === kind);
      return match ? t(match.label_key) : kind;
    },
    [profile, t],
  );

  const setView = (next: View) => {
    const copy = new URLSearchParams(params);
    if (next === "lista") copy.delete("vista");
    else copy.set("vista", next);
    setParams(copy, { replace: true });
  };

  if (loading && !data) {
    return (
      <Page title={t("inpatients.title")}>
        <Skeleton rows={5} />
      </Page>
    );
  }

  return (
    <Page
      title={t("inpatients.title")}
      subtitle={t("inpatients.subtitle", {
        count: rows.length,
        attention: data?.totals.attention ?? 0,
      })}
      actions={
        <>
          {/* Para pendurar na parede: tela cheia, sem menu, sem busca. Um
              mural com barra de navegação não é um mural. */}
          <Link to="/painel" className="nav-link">
            {t("inpatients.wallMode")}
          </Link>
          <Gate can={CAN.patientRegister}>
            <Button variant="secondary" onClick={() => setRegistering(true)}>
              {t("patients.list.register")}
            </Button>
          </Gate>
          <Gate can={CAN.hospitalizationAdmit}>
            <Button onClick={() => navigate("/internar")}>{t("patients.list.admit")}</Button>
          </Gate>
        </>
      }
    >
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}

      <div className="patients-toolbar">
        <div className="chip-group" role="group" aria-label={t("inpatients.viewLabel")}>
          {VIEWS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => setView(option)}
              className={view === option ? "chip chip-on" : "chip"}
            >
              {t(`inpatients.view.${option}`)}
            </button>
          ))}
        </div>

        <div className="patients-search">
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--ink-3)"
              strokeWidth="2"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <circle cx="11" cy="11" r="7" />
              <path d="M21 21l-4.3-4.3" />
            </svg>
          <input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={searchPlaceholder}
            aria-label={searchPlaceholder}
          />
        </div>
      </div>

      {view === "lista" ? (
        <>
          <div className="chip-group" role="group" aria-label={t("inpatients.filterLabel")}>
            {FILTERS.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={filter === option}
                onClick={() => setFilter(option)}
                className={filter === option ? "chip chip-on" : "chip"}
              >
                {t(`patients.filter.${option}`)}{" "}
                <span className="patients-count tabular">{counts[option]}</span>
              </button>
            ))}
          </div>
          <PatientTable rows={visible} total={rows.length} />
        </>
      ) : null}

      {view === "boxes" ? <KennelMap rows={rows} /> : null}

      {archiveError ? <ErrorState message={archiveError} /> : null}

      {offBoard.length > 0 ? (
        <Section title={t("patients.archive.title")} hint={t("patients.archive.hint")}>
          <div className="patients-hits">
            {offBoard.map((hit) => (
              <div key={hit.id} className="patients-hit">
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{hit.name}</div>
                  <div style={{ fontSize: 13, color: "var(--ink-3)" }}>
                    {[
                      hit.species,
                      hit.breed,
                      hit.owner_name,
                      ...hit.identifiers.map(
                        (identifier) => `${identifierLabel(identifier.kind)} ${identifier.value}`,
                      ),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
                {hit.active_hospitalization_id ? (
                  <Button
                    onClick={() => navigate(`/internacao/${hit.active_hospitalization_id}`)}
                  >
                    {t("patients.archive.open")}
                  </Button>
                ) : (
                  <PastStays patientId={hit.id} />
                )}
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      {registering ? (
        <PatientsQuickCreate
          owners={owners}
          profile={profile}
          onClose={() => setRegistering(false)}
          onCreated={(patient) => {
            setRegistering(false);
            // Cadastrar sem internar em seguida é raro: leva direto à admissão.
            navigate(`/internar?paciente=${patient.id}`);
          }}
        />
      ) : null}
    </Page>
  );
}

/** O que aconteceu com este paciente antes.
 *
 *  Dar alta fazia o paciente sumir do sistema: some do painel (correto: o
 *  painel é de quem está internado agora) e a busca só oferecia "Internar", ou
 *  seja, internar DE NOVO. Quem acabou de dar alta quer a conta e o prontuário
 *  daquela internação: é a próxima coisa que ele faz, e a lei dá cinco dias
 *  úteis para entregar a cópia ao tutor.
 */
function PastStays({ patientId }: { patientId: string }) {
  const { t } = useTranslation();
  const { day } = useClinic();
  const navigate = useNavigate();
  const [stays, setStays] = useState<Hospitalization[] | null>(null);

  useEffect(() => {
    let alive = true;
    void api
      .hospitalizations(patientId)
      .then((value) => alive && setStays(value))
      .catch(() => alive && setStays([]));
    return () => {
      alive = false;
    };
  }, [patientId]);

  const ultima = stays?.[0];
  return (
    <div className="past-stays">
      {ultima ? (
        <Button variant="secondary" onClick={() => navigate(`/internacao/${ultima.id}/conta`)}>
          {t("patients.archive.lastStay", {
            outcome: t(`outcome.${ultima.status}`),
            date: day(ultima.ended_at ?? ultima.admitted_at),
          })}
        </Button>
      ) : null}
      <Gate can={CAN.hospitalizationAdmit}>
        <Button onClick={() => navigate(`/internar?paciente=${patientId}`)}>
          {t("patients.archive.admit")}
        </Button>
      </Gate>
    </div>
  );
}

/** O censo. CALMO de propósito.
 *
 *  Esta tabela tinha o fundo vermelho em cinco linhas de cinco, com uma coluna
 *  "Situação" que dizia "Atrasada" nas cinco. Quando tudo é exceção, nada é,
 *  e a coluna que repete a mesma palavra em todas as linhas não carrega
 *  informação nenhuma, só tinta.
 *
 *  A separação: o **plantão** é a tela das exceções, e é lá que mora o
 *  vermelho e a ação. **Internados** responde "quem está aqui, onde, e como
 *  vai". É navegação. Cada linha diz o que a distingue das outras, numa frase,
 *  e a gravidade aparece na aresta e no texto, nunca só na cor.
 */
function PatientTable({ rows, total }: { rows: BoardRow[]; total: number }) {
  const { t } = useTranslation();
  const { time, duration } = useClinic();

  if (rows.length === 0) {
    return (
      <EmptyState
        title={total === 0 ? t("patients.list.empty") : t("patients.list.emptyFiltered")}
        hint={total === 0 ? t("patients.list.emptyHint") : undefined}
      />
    );
  }

  return (
    <div className="census">
      {rows.map((row) => {
        const attention = row.attention;
        const grave =
          attention?.reason === "critical_overdue" || attention?.reason === "overdue";
        const tone = grave ? "late" : attention ? "warn" : "calm";
        return (
          <Link
            key={row.hospitalization_id}
            to={`/internacao/${row.hospitalization_id}`}
            className={`census-row census-${tone}`}
          >
            <span className="census-who">
              <strong>{row.patient_name}</strong>
              <span className="census-meta">
                {[row.species, row.kennel_name].filter(Boolean).join(" · ") || "—"}
              </span>
            </span>

            {/* Uma frase, não uma etiqueta repetida. O que este paciente tem de
                diferente dos outros quatro. */}
            <span className="census-state">
              {attention
                ? t(`census.state.${attention.reason}`, {
                    task: attention.task_title,
                    elapsed:
                      attention.reason === "no_progress_note"
                        ? t("time.hours", { n: attention.magnitude ?? 0 })
                        : duration(attention.magnitude ?? 0),
                  })
                : row.next_task
                  ? t("census.state.on_time", {
                      task: row.next_task.title,
                      time: time(row.next_task.scheduled_for),
                    })
                  : t("census.state.idle")}
            </span>

            <span className="census-today tabular">
              {t("census.doneToday", {
                done: row.counters.done_today,
                total: row.counters.planned_today,
              })}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

/** O mapa de boxes. Junta por ID, não por nome.
 *
 *  O cruzamento anterior era `Map<kennel_name, BoardRow>`: dois boxes com o
 *  mesmo nome, ou um box renomeado enquanto ocupado, mostravam o paciente no
 *  box errado, num mapa cuja única função é dizer onde o animal está. */
function KennelMap({ rows }: { rows: BoardRow[] }) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();
  const [kennels, setKennels] = useState<Kennel[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setKennels(asList(await api.kennels()));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const byKennelId = useMemo(() => {
    const map = new Map<string, BoardRow>();
    for (const row of rows) if (row.kennel_id) map.set(row.kennel_id, row);
    return map;
  }, [rows]);

  const semBox = rows.filter((row) => !row.kennel_id);

  if (error) return <ErrorState message={error} onRetry={() => void load()} />;
  if (!kennels) return <Skeleton rows={2} height={90} />;

  const areas = new Map<string, Kennel[]>();
  for (const kennel of kennels) {
    const key = kennel.area ?? "";
    areas.set(key, [...(areas.get(key) ?? []), kennel]);
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      {kennels.length === 0 ? (
        <EmptyState title={t("kennels.empty")} hint={t("kennels.emptyHint")} />
      ) : null}

      {[...areas.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([area, list]) => {
          const ocupados = list.filter((kennel) => byKennelId.has(kennel.id)).length;
          return (
            <Section
              key={area || "__none__"}
              title={area || t("kennels.noArea")}
              hint={t("kennels.areaSummary", { occupied: ocupados, total: list.length })}
            >
              <div className="kennel-grid">
                {[...list]
                  .sort((a, b) => a.name.localeCompare(b.name))
                  .map((kennel) => {
                    const row = byKennelId.get(kennel.id);
                    const content = (
                      <>
                        <span className="kennel-name">{kennel.name}</span>
                        <span className="kennel-patient">
                          {row ? row.patient_name : t("kennels.vacant")}
                        </span>
                        <span className="kennel-hint">
                          {row?.attention
                            ? t(`kennels.status.${row.critical_overdue ? "critical" : "late"}`)
                            : row
                              ? t("kennels.status.ok")
                              : t("kennels.vacantHint")}
                        </span>
                      </>
                    );
                    return row ? (
                      <Link
                        key={kennel.id}
                        to={`/internacao/${row.hospitalization_id}`}
                        className={`kennel-card${row.attention ? " kennel-card-attention" : ""}`}
                      >
                        {content}
                      </Link>
                    ) : (
                      <div key={kennel.id} className="kennel-card kennel-card-free">
                        {content}
                      </div>
                    );
                  })}
              </div>
            </Section>
          );
        })}

      {/* Um internado sem box não pode sumir do mapa: é o caso em que alguém
          precisa agir. */}
      {semBox.length > 0 ? (
        <Section title={t("kennels.unassigned", { count: semBox.length })}>
          <div className="steady-list">
            {semBox.map((row) => (
              <Link
                key={row.hospitalization_id}
                to={`/internacao/${row.hospitalization_id}`}
                className="steady-chip"
              >
                <strong>{row.patient_name}</strong>
              </Link>
            ))}
          </div>
        </Section>
      ) : null}
    </div>
  );
}
