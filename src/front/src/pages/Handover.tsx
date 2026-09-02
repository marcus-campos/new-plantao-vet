import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import "../styles/handover.css";

import { ApiError, api, asList } from "../api/client";
import type { HandoverReport, MembershipRoster, Shift, Task } from "../api/types";
import { PinDialog } from "../components/PinDialog";
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  Page,
  Section,
  Skeleton,
  useApiErrorMessage,
} from "../components/ui";
import { COUNTER_KEYS, skeletonCounters, skeletonNoteCount } from "../components/handoverShared";
import { useClinic } from "../hooks/useClinic";
import { useSession } from "../hooks/useSession";

/** Passagem de plantão.
 *
 *  São DOIS atos opostos, e a tela mostrava um só: o mesmo cartão, com os
 *  mesmos botões, para todo mundo, o tempo inteiro. Parecia que você estava
 *  sempre recebendo.
 *
 *  **Entregar** é olhar para trás: o que aconteceu no MEU turno, revisar o
 *  texto (ou escrevê-lo eu mesmo; não havia como), aprovar paciente a paciente
 *  e ir embora. O verbo é *revisar e aprovar*.
 *
 *  **Receber** é olhar para frente: o que a pessoa anterior deixou, quais
 *  pendências eu estou assumindo, e aceitar. O verbo é *li e assumo*.
 *
 *  Qual dos dois aparece não é uma preferência: sai da escala. Se um turno meu
 *  está começando e há boletim endereçado a ele, eu recebo; se um turno meu
 *  está terminando, eu entrego. O seletor existe porque o sistema pode errar,
 *  e porque às vezes se quer olhar o outro lado.
 */
type Side = "receive" | "deliver";

export function Handover() {
  const { t } = useTranslation();
  const { time, duration } = useClinic();
  const describeError = useApiErrorMessage();
  const { me } = useSession();

  const [shifts, setShifts] = useState<Shift[]>([]);
  const [roster, setRoster] = useState<MembershipRoster[]>([]);
  const [reports, setReports] = useState<HandoverReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [side, setSide] = useState<Side | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [pinRetry, setPinRetry] = useState<(() => void) | null>(null);

  const load = useCallback(async () => {
    try {
      const desde = new Date(Date.now() - 3 * 86_400_000).toISOString();
      const [shiftData, reportData] = await Promise.all([
        // Sem janela, a rota devolve os 50 turnos MAIS ANTIGOS e "de plantão
        // agora" fica permanentemente vazio depois de dois meses.
        api.shifts({ from: desde, limit: 50 }),
        api.handoverReports(),
      ]);
      setShifts(asList(shiftData));
      setReports(asList(reportData));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
    try {
      // O roster é legível por qualquer membro e não traz e-mail nem PIN. A
      // tela usava `GET /memberships`, que é do administrador, dentro de um
      // catch silencioso, então o bastão mostrava "– → –" justamente para
      // quem precisa dele.
      setRoster(await api.membershipRoster());
    } catch {
      // Sem nomes a passagem continua legível; é o único degrade aceitável aqui.
      setRoster([]);
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const meusTurnos = useMemo(
    () => new Set(shifts.filter((s) => s.membership_id === me?.membership_id).map((s) => s.id)),
    [shifts, me],
  );
  const nomePorTurno = useMemo(() => {
    const pessoas = new Map(roster.map((p) => [p.id, p.name]));
    return new Map(shifts.map((s) => [s.id, pessoas.get(s.membership_id) ?? ""]));
  }, [shifts, roster]);
  const turnoPorId = useMemo(() => new Map(shifts.map((s) => [s.id, s])), [shifts]);

  /** Os turnos que ENTRAM junto com o meu.
   *
   *  Entram duas pessoas no mesmo plantão, a veterinária e a técnica, e o
   *  boletim só aponta para um turno (o do veterinário responsável, que é quem
   *  responde perante o conselho). Sem isto, a técnica que assume o mesmo
   *  plantão não veria passagem nenhuma para aceitar. Quem entra na mesma
   *  janela está assumindo os mesmos pacientes. */
  const turnosDoMesmoPlantao = useMemo(() => {
    const meus = shifts.filter((s) => meusTurnos.has(s.id));
    const juntos = new Set(meusTurnos);
    for (const meu of meus) {
      const inicio = new Date(meu.starts_at).getTime();
      const fim = new Date(meu.ends_at).getTime();
      for (const outro of shifts) {
        const oInicio = new Date(outro.starts_at).getTime();
        if (oInicio < fim && new Date(outro.ends_at).getTime() > inicio) juntos.add(outro.id);
      }
    }
    return juntos;
  }, [shifts, meusTurnos]);

  /** UMA passagem, não o histórico.
   *
   *  A tela somava todos os boletins já endereçados a mim e mostrava "5 de 13
   *  aceitos", com o bastão apontando para os turnos de anteontem. Passagem de
   *  plantão é um ato entre dois turnos: o que importa é a troca de agora. Fica
   *  a mais recente: a anterior já foi aceita e virou histórico.
   *
   *  A ordem sai do turno que entrega, não de `created_at`: dois boletins do
   *  mesmo fechamento nascem no mesmo instante. */
  const maisRecente = useCallback(
    (lista: HandoverReport[], chave: "from_shift_id" | "to_shift_id") => {
      if (lista.length === 0) return lista;
      const quando = (id: string | null) => {
        const turno = id ? turnoPorId.get(id) : null;
        return turno ? new Date(turno.starts_at).getTime() : 0;
      };
      const alvo = lista.reduce((a, b) =>
        quando(b[chave]) > quando(a[chave]) ? b : a,
      )[chave];
      return lista.filter((r) => r[chave] === alvo);
    },
    [turnoPorId],
  );

  const aReceber = useMemo(
    () =>
      maisRecente(
        reports.filter((r) => r.to_shift_id && turnosDoMesmoPlantao.has(r.to_shift_id)),
        "from_shift_id",
      ),
    [reports, turnosDoMesmoPlantao, maisRecente],
  );
  const aEntregar = useMemo(
    () =>
      maisRecente(
        reports.filter((r) => r.from_shift_id && meusTurnos.has(r.from_shift_id)),
        "from_shift_id",
      ),
    [reports, meusTurnos, maisRecente],
  );

  // O lado sai da escala, não de uma preferência. Pendência decide o empate:
  // se tenho boletim para aceitar, é isso que estou fazendo agora.
  const ladoSugerido: Side = useMemo(() => {
    // Entregar vem primeiro: quem tem boletim seu por aprovar acabou de fechar
    // o turno, e revisar o que aconteceu precede assumir o que vem. Na troca,
    // a mesma pessoa costuma estar dos dois lados (a técnica que sai às 23h
    // também entra no turno seguinte) e a tela abria dizendo "você está
    // recebendo" para quem estava indo embora.
    if (aEntregar.some((r) => !r.reviewed_at)) return "deliver";
    if (aReceber.some((r) => !r.acked_at)) return "receive";
    return aEntregar.length > 0 ? "deliver" : "receive";
  }, [aReceber, aEntregar]);

  const lado = side ?? ladoSugerido;
  const lista = lado === "receive" ? aReceber : aEntregar;

  const run = useCallback(
    async (acao: () => Promise<unknown>, reportId: string) => {
      setBusy(reportId);
      try {
        await acao();
        await load();
        setError(null);
      } catch (err) {
        if (err instanceof ApiError && err.code === "operator_required") {
          setPinRetry(() => () => void run(acao, reportId));
          return;
        }
        setError(describeError(err));
      } finally {
        setBusy(null);
      }
    },
    [load, describeError],
  );

  if (loading) {
    return (
      <Page title={t("handover.title")}>
        <Skeleton rows={3} height={120} />
      </Page>
    );
  }

  // Nenhum turno meu envolvido: não estou nem entregando nem recebendo. Dizer
  // isso é melhor que mostrar botões que não são meus.
  if (aReceber.length === 0 && aEntregar.length === 0) {
    return (
      <Page title={t("handover.title")}>
        {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        <EmptyState
          title={t("handover.notYours")}
          hint={t("handover.notYoursHint")}
          action={
            <Link to="/plantao" className="nav-link">
              {t("handover.goToShift")}
            </Link>
          }
        />
      </Page>
    );
  }

  const feitos = lista.filter((r) => (lado === "receive" ? r.acked_at : r.reviewed_at)).length;
  const completo = lista.length > 0 && feitos === lista.length;

  const turnoOrigem = turnoPorId.get(lista[0]?.from_shift_id ?? "");
  const turnoDestino = turnoPorId.get(lista[0]?.to_shift_id ?? "");

  return (
    <Page
      title={
        lado === "receive"
          ? completo
            ? t("handover.receivedAll")
            : t("handover.receiveTitle", { done: feitos, total: lista.length })
          : completo
            ? t("handover.deliveredAll")
            : t("handover.deliverTitle", { done: feitos, total: lista.length })
      }
      eyebrow={t(lado === "receive" ? "handover.receiveEyebrow" : "handover.deliverEyebrow")}
      subtitle={
        lado === "receive"
          ? t("handover.receiveSubtitle", {
              from: nomePorTurno.get(turnoOrigem?.id ?? "") || t("handover.someone"),
              shift: turnoOrigem?.name ?? "–",
            })
          : t("handover.deliverSubtitle", {
              to: nomePorTurno.get(turnoDestino?.id ?? "") || t("handover.nobodyYet"),
              shift: turnoDestino?.name ?? "–",
            })
      }
      actions={
        // Só aparece quando os dois lados existem de verdade. Um seletor com um
        // botão só é ruído.
        aReceber.length > 0 && aEntregar.length > 0 ? (
          <div className="chip-group" role="group" aria-label={t("handover.sideLabel")}>
            {(["receive", "deliver"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={lado === option}
                onClick={() => setSide(option)}
                className={lado === option ? "chip chip-on" : "chip"}
              >
                {t(`handover.side.${option}`)}
              </button>
            ))}
          </div>
        ) : null
      }
    >
      {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}

      <div className="hv-baton">
        <div className="hv-head-title">
          <span className="eyebrow">{t("handover.deliver")}</span>
          <strong>
            {nomePorTurno.get(turnoOrigem?.id ?? "") || "–"}
            {turnoOrigem
              ? ` · ${turnoOrigem.name} ${time(turnoOrigem.starts_at)}–${time(turnoOrigem.ends_at)}`
              : ""}
          </strong>
        </div>
        <span className="hv-baton-arrow" aria-hidden="true">
          →
        </span>
        <div className="hv-head-title">
          <span className="eyebrow">{t("handover.receive")}</span>
          <strong>
            {nomePorTurno.get(turnoDestino?.id ?? "") || t("handover.nobodyYet")}
            {turnoDestino
              ? ` · ${turnoDestino.name} ${time(turnoDestino.starts_at)}–${time(turnoDestino.ends_at)}`
              : ""}
          </strong>
        </div>
      </div>

      {completo ? (
        <EmptyState
          tone="good"
          title={t(lado === "receive" ? "handover.receivedAll" : "handover.deliveredAll")}
          hint={t(lado === "receive" ? "handover.receivedHint" : "handover.deliveredHint")}
          action={
            <Link to="/plantao" className="nav-link">
              {t(lado === "receive" ? "handover.goToShift" : "handover.goHome")}
            </Link>
          }
        />
      ) : null}

      <Section
        title={t("handover.reportsTitle")}
        hint={t(lado === "receive" ? "handover.receiveHint" : "handover.deliverHint")}
      >
        <div className="hv-grid">
          {lista.map((report) =>
            lado === "receive" ? (
              <ReceiveCard
                key={report.id}
                report={report}
                busy={busy === report.id}
                duration={duration}
                onAck={(seconds) =>
                  void run(() => api.ackHandover(report.id, seconds), report.id)
                }
              />
            ) : (
              <DeliverCard
                key={report.id}
                report={report}
                busy={busy === report.id}
                onDraft={() => void run(() => api.setNarrative(report.id), report.id)}
                onSave={(text) => void run(() => api.setNarrative(report.id, text), report.id)}
                onApprove={() => void run(() => api.approveHandover(report.id), report.id)}
              />
            ),
          )}
        </div>
      </Section>

      {pinRetry ? (
        <PinDialog
          onDone={() => {
            const retry = pinRetry;
            setPinRetry(null);
            retry();
          }}
          onCancel={() => setPinRetry(null)}
        />
      ) : null}
    </Page>
  );
}

/** Quem RECEBE. Só leitura, mais o que está sendo assumido. */
function ReceiveCard({
  report,
  busy,
  duration,
  onAck,
}: {
  report: HandoverReport;
  busy: boolean;
  duration: (minutes: number) => string;
  onAck: (seconds: number) => void;
}) {
  const { t } = useTranslation();
  const { time } = useClinic();
  // O tempo até o aceite é medido a partir de quando ESTE cartão foi aberto.
  // Marcado na montagem da tela, o quinto paciente registrava o tempo de
  // leitura dos quatro anteriores, e o número existe para ser o termômetro de
  // "carimbo em série".
  const [abertoEm] = useState(() => Date.now());
  const revisado = report.reviewed_at !== null;

  return (
    <article className={`hv-card${revisado ? "" : " hv-card-unreviewed"}`}>
      <CardHead report={report} />

      {!revisado ? (
        <div className="hv-alert">
          <span className="hv-alert-title">{t("handover.unreviewed")}</span>
          <p className="hv-alert-text">{t("handover.unreviewedHint")}</p>
        </div>
      ) : null}

      <div className="hv-narrative">
        <span className="eyebrow">
          {t(revisado ? "handover.narrativeTitle" : "handover.narrativeUnreviewed")}
        </span>
        <p className="hv-narrative-text">{report.narrative || t("handover.noNarrative")}</p>
      </div>

      {/* O elemento nº 1 do I-PASS: quem assume vê O QUE está assumindo, escrito,
          no próprio ato. Contadores não são pendências. */}
      <OpenTasks tasks={report.open_tasks} duration={duration} mine />

      <div className="hv-actions">
        {report.acked_at ? (
          <span className="hv-seal">
            {t("handover.ackedBy", {
              name: report.acked_by_name ?? "",
              time: time(report.acked_at),
            })}
          </span>
        ) : (
          <Button
            disabled={busy}
            onClick={() => onAck(Math.max(0, Math.round((Date.now() - abertoEm) / 1000)))}
          >
            {t("handover.ack")}
          </Button>
        )}
      </div>
    </article>
  );
}

/** Quem ENTREGA. Escreve o texto, revisa e aprova. */
function DeliverCard({
  report,
  busy,
  onDraft,
  onSave,
  onApprove,
}: {
  report: HandoverReport;
  busy: boolean;
  onDraft: () => void;
  onSave: (text: string) => void;
  onApprove: () => void;
}) {
  const { t } = useTranslation();
  const { time } = useClinic();
  const [text, setText] = useState(report.narrative ?? "");
  const [editing, setEditing] = useState(false);
  const aprovado = report.reviewed_at !== null;

  // O texto pode ter mudado no servidor (rascunho gerado): se não estou
  // editando, sigo o servidor em vez de mostrar um rascunho velho.
  useEffect(() => {
    if (!editing) setText(report.narrative ?? "");
  }, [report.narrative, editing]);

  return (
    <article className="hv-card">
      <CardHead report={report} />

      <div className="hv-narrative">
        <span className="eyebrow">{t("handover.yourSummary")}</span>
        {aprovado ? (
          <p className="hv-narrative-text">{report.narrative || t("handover.noNarrative")}</p>
        ) : (
          <>
            <textarea
              className="hv-editor"
              value={text}
              rows={5}
              onChange={(event) => {
                setEditing(true);
                setText(event.target.value);
              }}
              placeholder={t("handover.writePlaceholder")}
              aria-label={t("handover.yourSummary")}
            />
            <div className="hv-editor-actions">
              {/* Gerar é AJUDA, não o caminho: quem assina é quem entrega. */}
              <Button variant="secondary" disabled={busy} onClick={onDraft}>
                {t("handover.draft")}
              </Button>
              <Button
                variant="secondary"
                disabled={busy || text.trim() === (report.narrative ?? "").trim()}
                onClick={() => {
                  setEditing(false);
                  onSave(text.trim());
                }}
              >
                {t("handover.save")}
              </Button>
            </div>
          </>
        )}
        {skeletonNoteCount(report.skeleton) > 0 ? (
          <span className="hv-muted">
            {t("handover.notesCount", { n: skeletonNoteCount(report.skeleton) })}
          </span>
        ) : null}
      </div>

      <OpenTasks tasks={report.open_tasks} duration={() => ""} mine={false} />

      <div className="hv-actions">
        {aprovado ? (
          <span className="hv-seal">
            {t("handover.approved", { time: time(report.reviewed_at ?? report.created_at) })}
          </span>
        ) : (
          <Button disabled={busy} onClick={onApprove}>
            {t("handover.approve")}
          </Button>
        )}
        {report.acked_at ? (
          <span className="hv-seal">
            {t("handover.ackedBy", {
              name: report.acked_by_name ?? "",
              time: time(report.acked_at),
            })}
          </span>
        ) : (
          <span className="hv-muted">{t("handover.awaitingAck")}</span>
        )}
      </div>
    </article>
  );
}

function CardHead({ report }: { report: HandoverReport }) {
  const { t } = useTranslation();
  const counters = skeletonCounters(report.skeleton);
  return (
    <div className="hv-card-head">
      <div className="hv-card-who">
        <strong>{report.patient_name ?? t("handover.patientMissing")}</strong>
        {report.kennel_name ? <span>{report.kennel_name}</span> : null}
        <Link to={`/internacao/${report.hospitalization_id}`} className="hv-open">
          {t("handover.openSheet")}
        </Link>
      </div>
      <div className="hv-chips">
        {COUNTER_KEYS.filter((key) => key !== "pending" && key !== "overdue").map((key) =>
          counters[key] === 0 && key !== "done" ? null : (
            <span key={key} className={`badge badge-${key === "done" ? "good" : "neutral"}`}>
              {t(`handover.counters.${key}`, { n: counters[key] })}
            </span>
          ),
        )}
      </div>
    </div>
  );
}

/** O que fica em aberto: a dívida do turno que sai, não a agenda do que entra. */
function OpenTasks({
  tasks,
  duration,
  mine,
}: {
  tasks: Task[];
  duration: (minutes: number) => string;
  mine: boolean;
}) {
  const { t } = useTranslation();
  const { time } = useClinic();

  if (tasks.length === 0) {
    return (
      <div className="hv-open-none">
        <span className="eyebrow">{t("handover.nothingOpen")}</span>
        <p className="hv-muted">{t("handover.nothingOpenHint")}</p>
      </div>
    );
  }

  return (
    <div className="hv-open-tasks">
      <span className="eyebrow">
        {t(mine ? "handover.youTakeOver" : "handover.youLeave", { count: tasks.length })}
      </span>
      <ul>
        {tasks.map((task) => {
          const atrasada = task.display_state === "overdue";
          return (
            <li key={task.id} className={atrasada ? "hv-open-late" : undefined}>
              <span className="tabular">{time(task.scheduled_for)}</span>
              <span>{task.title}</span>
              {task.criticality === "critical" ? (
                <Badge tone="late">{t("sheet.criticality.critical")}</Badge>
              ) : null}
              {atrasada ? (
                <em>
                  {t("shift.lateBy", {
                    elapsed: duration(
                      Math.max(
                        0,
                        Math.round((Date.now() - new Date(task.scheduled_for).getTime()) / 60_000),
                      ),
                    ),
                  })}
                </em>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
