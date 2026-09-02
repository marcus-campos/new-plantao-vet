import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import "../styles/shift.css";

import { ApiError, api } from "../api/client";
import type { Board, BoardRow, Task } from "../api/types";
import { NotDoneDialog } from "../components/NotDoneDialog";
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
import { useBoard } from "../hooks/useBoard";
import { useClinic } from "../hooks/useClinic";
import { useSession } from "../hooks/useSession";

/** O console do plantão.
 *
 *  A pergunta é uma só: **o que precisa de mim agora**. Mas a resposta não é
 *  uma lista de tarefas: é uma VOLTA.
 *
 *  Quem está de plantão não processa uma fila: anda pela enfermaria. Vai até o
 *  box, resolve tudo daquele animal enquanto está de pé ali, e passa para o
 *  próximo. Uma fila cronológica intercala Nina, Thor, Mel, Nina, Mel e obriga
 *  a pessoa a ziguezaguear pelo corredor, ou a reordenar tudo de cabeça antes
 *  de sair do lugar. Agrupar por paciente não é preferência de layout: é como o
 *  corpo se move pela sala.
 *
 *  Daí as três decisões desta tela:
 *
 *  1. **Um cartão por paciente, não por tarefa.** A manchete conta PACIENTES
 *     ("5 precisam de atenção"), porque são cinco boxes para visitar. "26
 *     tarefas agora" é ansiedade, não informação.
 *  2. **Só o primeiro vem aberto.** Os outros são uma linha até você chegar
 *     neles. Vinte e seis cartões abertos com dois botões cada são cinquenta e
 *     dois botões numa tela, o oposto de saber o que fazer.
 *  3. **O que vem depois é horizonte, não trabalho.** "Próxima hora" e
 *     "depois" ficam recolhidos numa linha: ninguém age sobre eles agora, e
 *     mostrá-los abertos só empurra para baixo o que importa.
 *
 *  E a volta tem fim. Quando o último cartão é limpo, a tela diz que acabou e
 *  quando é a próxima, em vez de deixar a pessoa procurando o que sobrou.
 */
export function ShiftConsole() {
  const { t } = useTranslation();
  const { time } = useClinic();
  const describeError = useApiErrorMessage();
  const { needsOperator } = useSession();
  const board = useBoard();

  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const loadQueue = useCallback(async () => {
    try {
      setTasks((await api.tasks()).items);
      setQueueError(null);
    } catch (err) {
      setQueueError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void loadQueue();
  }, [loadQueue]);

  const refresh = useCallback(async () => {
    await Promise.all([loadQueue(), board.reload()]);
  }, [loadQueue, board]);

  const execute = useCallback(
    async (task: Task, options: { confirm_early?: boolean } = {}) => {
      setBusy(task.id);
      try {
        await api.executeTask(task.id, options);
        await refresh();
      } catch (err) {
        if (err instanceof ApiError && err.code === "operator_required") {
          setDialog({ kind: "pin", context: task.title, retry: () => void execute(task, options) });
          return;
        }
        if (err instanceof ApiError && err.code === "early_confirmation_required") {
          setDialog({ kind: "early", task });
          return;
        }
        setQueueError(describeError(err));
      } finally {
        setBusy(null);
      }
    },
    [refresh, describeError],
  );

  const openNotDone = useCallback(
    (task: Task) => {
      if (needsOperator) {
        setDialog({ kind: "pin", context: task.title, retry: () => setDialog({ kind: "notDone", task }) });
        return;
      }
      setDialog({ kind: "notDone", task });
    },
    [needsOperator],
  );

  const rounds = useMemo(() => buildRounds(board.data, tasks), [board.data, tasks]);

  const turno = board.data?.shifts.find((shift) => shift.is_mine) ?? board.data?.shifts[0] ?? null;

  if (board.loading && !board.data) {
    return (
      <Page title={t("shift.title")}>
        <Skeleton rows={4} />
      </Page>
    );
  }

  const proxima = rounds.ahead[0];

  return (
    <Page
      title={
        rounds.now.length > 0
          ? t("shift.needAttention", { count: rounds.now.length })
          : t("shift.allClear")
      }
      eyebrow={
        turno
          ? t("shift.onDuty", {
              name: turno.member_name ?? "—",
              shift: turno.name,
              until: time(turno.ends_at),
            })
          : t("shift.noShift")
      }
      subtitle={
        board.fetchedAt
          ? t("shift.updatedAt", {
              time: time(board.fetchedAt),
              patients: board.data?.totals.patients ?? 0,
            })
          : undefined
      }
      actions={
        <Link to="/passagem" className="nav-link">
          {t("shift.goToHandover")}
        </Link>
      }
    >
      {board.error ? <ErrorState message={board.error} onRetry={() => void board.reload()} /> : null}
      {queueError ? <ErrorState message={queueError} onRetry={() => void loadQueue()} /> : null}

      <Section
        title={t("shift.roundTitle")}
        hint={rounds.now.length > 0 ? t("shift.roundHint") : undefined}
      >
        {rounds.now.length === 0 ? (
          <EmptyState
            tone="good"
            title={t("shift.roundDone")}
            hint={
              proxima
                ? t("shift.roundNext", {
                    time: time(proxima.task.scheduled_for),
                    patient: proxima.patient,
                    task: proxima.task.title,
                  })
                : t("shift.nothingSoon")
            }
          />
        ) : (
          <div className="round">
            {rounds.now.map((stop) => (
              <RoundStop
                key={stop.row.hospitalization_id}
                stop={stop}
                busy={busy}
                onExecute={(task) => void execute(task)}
                onNotDone={openNotDone}
              />
            ))}
          </div>
        )}
      </Section>

      <Ahead ahead={rounds.ahead} later={rounds.later} />

      {rounds.steady.length > 0 ? (
        <Section title={t("shift.steadyTitle", { count: rounds.steady.length })}>
          <div className="steady-list">
            {rounds.steady.map((row) => (
              <Link
                key={row.hospitalization_id}
                to={`/internacao/${row.hospitalization_id}`}
                className="steady-chip"
              >
                <strong>{row.patient_name}</strong>
                <span>{row.kennel_name ?? "—"}</span>
                {row.next_task ? (
                  <span className="tabular steady-next">{time(row.next_task.scheduled_for)}</span>
                ) : null}
              </Link>
            ))}
          </div>
        </Section>
      ) : null}

      {dialog?.kind === "pin" ? (
        <PinDialog
          context={dialog.context}
          onDone={() => {
            const retry = dialog.retry;
            setDialog(null);
            retry();
          }}
          onCancel={() => setDialog(null)}
        />
      ) : null}

      {dialog?.kind === "early" ? (
        <ConfirmEarly
          task={dialog.task}
          time={time(dialog.task.scheduled_for)}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const task = dialog.task;
            setDialog(null);
            void execute(task, { confirm_early: true });
          }}
        />
      ) : null}

      {dialog?.kind === "notDone" ? (
        <NotDoneDialog
          task={dialog.task}
          onClose={() => setDialog(null)}
          onDone={async () => {
            setDialog(null);
            await refresh();
          }}
          onError={setQueueError}
        />
      ) : null}
    </Page>
  );
}

type Dialog =
  | { kind: "pin"; context?: string; retry: () => void }
  | { kind: "early"; task: Task }
  | { kind: "notDone"; task: Task };

interface Stop {
  row: BoardRow;
  tasks: Task[];
}

/** A volta, montada a partir das duas fontes que já existem.
 *
 *  O painel diz QUEM precisa de atenção e por quê; a fila diz O QUE está em
 *  aberto. Antes eram duas seções na tela mostrando os mesmos pacientes (a
 *  lista de atenção e a fila), e a pessoa lia o nome da Nina duas vezes sem que
 *  a segunda acrescentasse nada. É um dado só, visto de dois lados. */
function buildRounds(board: Board | null, tasks: Task[] | null) {
  const porInternacao = new Map<string, Task[]>();
  for (const task of tasks ?? []) {
    if (task.status !== "pending") continue;
    if (task.display_state !== "due" && task.display_state !== "overdue") continue;
    porInternacao.set(task.hospitalization_id, [
      ...(porInternacao.get(task.hospitalization_id) ?? []),
      task,
    ]);
  }

  const rows = board?.rows ?? [];
  const now: Stop[] = [];
  const steady: BoardRow[] = [];
  for (const row of rows) {
    const abertas = (porInternacao.get(row.hospitalization_id) ?? []).sort(
      (a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime(),
    );
    // O paciente entra na volta se tem tarefa vencida OU um motivo que não é
    // tarefa: sem evolução há 24h é obrigação do conselho e some da fila
    // cronológica porque não é uma dose.
    if (abertas.length > 0 || row.attention) now.push({ row, tasks: abertas });
    else steady.push(row);
  }

  const limite = Date.now() + 3_600_000;
  const nomes = new Map(rows.map((row) => [row.hospitalization_id, row.patient_name]));
  const futuras = (tasks ?? [])
    .filter((task) => task.status === "pending" && task.display_state === "on_time")
    .sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime())
    .map((task) => ({ task, patient: nomes.get(task.hospitalization_id) ?? "" }));

  return {
    now,
    steady,
    ahead: futuras.filter((item) => new Date(item.task.scheduled_for).getTime() <= limite),
    later: futuras.filter((item) => new Date(item.task.scheduled_for).getTime() > limite),
  };
}

/** Uma parada da volta: o paciente, o motivo e tudo que ele precisa agora.
 *
 *  Sem acordeão. Tentei esconder as tarefas atrás de um clique para evitar o
 *  paredão, mas o paredão vinha de NÃO agrupar por paciente, e o agrupamento
 *  já o resolveu. O que a dobra acrescentou foi um clique a mais para chegar no
 *  paciente e a chance de abrir vazio.
 *
 *  O cabeçalho é o link para a ficha: um clique, como antes. As tarefas estão
 *  ali, prontas para a baixa. */
function RoundStop({
  stop,
  busy,
  onExecute,
  onNotDone,
}: {
  stop: Stop;
  busy: string | null;
  onExecute: (task: Task) => void;
  onNotDone: (task: Task) => void;
}) {
  const { t } = useTranslation();
  const { time, duration } = useClinic();
  const { row, tasks } = stop;
  const attention = row.attention;
  const grave = attention?.reason === "critical_overdue" || attention?.reason === "overdue";

  // Um paciente com quinze doses abertas não pode empurrar os outros quatro
  // para fora da tela. As primeiras são a volta; o resto está na ficha.
  const [showAll, setShowAll] = useState(false);
  const LIMITE = 5;
  const visiveis = showAll ? tasks : tasks.slice(0, LIMITE);
  const escondidas = tasks.length - visiveis.length;

  return (
    <article className={`stop${grave ? " stop-late" : ""}`}>
      <Link to={`/internacao/${row.hospitalization_id}`} className="stop-head">
        <span className="stop-who">
          <strong>{row.patient_name}</strong>
          <span>{row.kennel_name ?? t("shift.noKennel")}</span>
        </span>
        <span className="stop-why">
          {attention
            ? t(`shift.reason.${attention.reason}`, {
                task: attention.task_title,
                elapsed:
                  attention.reason === "no_progress_note"
                    ? t("time.hours", { n: attention.magnitude ?? 0 })
                    : duration(attention.magnitude ?? 0),
              })
            : null}
        </span>
        <span className="stop-count">
          {tasks.length > 0 ? t("shift.thingsToDo", { count: tasks.length }) : t("shift.open")}
        </span>
      </Link>

      {tasks.length > 0 || attention?.reason === "no_progress_note" ? (
        <div className="stop-body">
          {visiveis.map((task) => (
            <div key={task.id} className={`todo todo-${task.display_state}`}>
              <span className="tabular todo-time">{time(task.scheduled_for)}</span>
              <span className="todo-what">
                {task.title}
                {task.criticality === "critical" ? (
                  <Badge tone="late">{t("sheet.criticality.critical")}</Badge>
                ) : null}
              </span>
              <span className="todo-state">
                {task.display_state === "overdue"
                  ? t("shift.lateBy", { elapsed: duration(minutesLate(task)) })
                  : t("state.due")}
              </span>
              <span className="todo-actions">
                <button
                  type="button"
                  className="todo-do"
                  disabled={busy === task.id}
                  onClick={() => onExecute(task)}
                >
                  {t("task.execute")}
                </button>
                <button
                  type="button"
                  className="todo-skip"
                  disabled={busy === task.id}
                  onClick={() => onNotDone(task)}
                  aria-label={`${t("task.notDone")} — ${task.title}`}
                  title={t("task.notDone")}
                >
                  ✕
                </button>
              </span>
            </div>
          ))}

          {escondidas > 0 ? (
            <button type="button" className="stop-more" onClick={() => setShowAll(true)}>
              {t("shift.showMore", { count: escondidas })}
            </button>
          ) : null}

          {/* Motivo que não é tarefa: a evolução do dia não nasce de
              aprazamento, então não aparece na fila, e é obrigação do
              conselho. Aqui ela tem o mesmo peso das doses. */}
          {attention?.reason === "no_progress_note" ? (
            <Link to={`/internacao/${row.hospitalization_id}/evolucao`} className="todo todo-note">
              <span className="todo-what">{t("shift.writeNote")}</span>
              <span className="todo-actions">
                <span className="todo-do">{t("shift.open")}</span>
              </span>
            </Link>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function minutesLate(task: Task): number {
  return Math.max(0, Math.round((Date.now() - new Date(task.scheduled_for).getTime()) / 60_000));
}

/** O horizonte. Recolhido, porque ninguém age sobre ele agora.
 *
 *  Mostrar quarenta e cinco tarefas futuras abertas empurra para fora da tela
 *  as cinco que importam. Uma linha basta para a pessoa saber que o turno tem
 *  fôlego, e ela abre se quiser se preparar. */
function Ahead({
  ahead,
  later,
}: {
  ahead: { task: Task; patient: string }[];
  later: { task: Task; patient: string }[];
}) {
  const { t } = useTranslation();
  const { time, moment } = useClinic();
  const [open, setOpen] = useState(false);

  if (ahead.length === 0 && later.length === 0) return null;

  return (
    <section className="ahead">
      <button type="button" className="ahead-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="eyebrow">{t("shift.aheadTitle")}</span>
        <span className="ahead-summary">
          {t("shift.aheadSummary", { next: ahead.length, later: later.length })}
        </span>
        <span className="ahead-toggle">{open ? t("shift.hide") : t("shift.show")}</span>
      </button>

      {open ? (
        <ul className="ahead-list">
          {[...ahead, ...later].slice(0, 40).map(({ task, patient }) => (
            <li key={task.id}>
              <span className="tabular ahead-time">{moment(task.scheduled_for)}</span>
              <span className="ahead-what">{task.title}</span>
              <Link to={`/internacao/${task.hospitalization_id}`} className="ahead-who">
                {patient}
              </Link>
            </li>
          ))}
        </ul>
      ) : ahead.length > 0 ? (
        <p className="ahead-peek">
          {t("shift.aheadPeek", {
            time: time(ahead[0].task.scheduled_for),
            task: ahead[0].task.title,
            patient: ahead[0].patient,
          })}
        </p>
      ) : null}
    </section>
  );
}

function ConfirmEarly({
  task,
  time,
  onConfirm,
  onCancel,
}: {
  task: Task;
  time: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{task.title}</h2>
        <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14 }}>
          {t("task.confirmEarly", { time })}
        </p>
        <p style={{ margin: 0, color: "var(--ink-3)", fontSize: 13 }}>{t("task.earlyHint")}</p>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="secondary" onClick={onCancel}>
            {t("common.cancel")}
          </Button>
          <Button onClick={onConfirm}>{t("task.executeAnyway")}</Button>
        </div>
      </div>
    </div>
  );
}
