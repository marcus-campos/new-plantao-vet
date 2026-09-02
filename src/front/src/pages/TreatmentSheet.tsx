import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import "../styles/sheet.css";

import { ApiError, api } from "../api/client";
import { CAN } from "../api/capabilities";
import type { Criticality, Prescription, PrescriptionCategory, Task } from "../api/types";
import { Gate } from "../components/authz";
import { NotDoneDialog } from "../components/NotDoneDialog";
import { PinDialog } from "../components/PinDialog";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  ErrorState,
  Field,
  Section,
  inputStyle,
  stateColors,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { useSession } from "../hooks/useSession";
import { usePatientContext } from "./Patient";

/** A ficha da internação: a grade HORA × TAREFA, agrupada por categoria.
 *
 *  É o equivalente digital da prancheta presa ao box, e o que existia aqui não
 *  era uma grade: era uma lista vertical de prescrições com uma fita de chips
 *  colados um no outro. Faltavam os DOIS eixos que organizam o papel (o tempo
 *  e a categoria), e a ausência é justamente o sinal clínico: no papel, a
 *  célula vazia numa hora que já passou É o atraso. Com os chips contíguos,
 *  buraco nenhum aparecia.
 *
 *  Três consertos de fundo vêm junto:
 *
 *  1. O cabeçalho do paciente saiu daqui: `Patient.tsx` o mantém na tela o
 *     tempo inteiro, e refazer `api.hospitalization()` numa aba que já recebe o
 *     detalhe pelo contexto era buscar duas vezes o mesmo dado.
 *  2. A hora REAL da execução aparece na célula. `executed_at` sempre existiu e
 *     nenhuma tela mostrava: um eMAR sem a hora do que foi feito registra
 *     intenção, não ato. (`executed_by` é uuid cru e a API não resolve nome,
 *     por isso mostramos a hora, nunca um identificador que ninguém lê.)
 *  3. Metade do domínio era inalcançável pelo cliente: dose PRN, execução
 *     parcial, registro retroativo, suspensão e titulação estão completos e
 *     auditados no servidor e nenhuma tela os chamava. Titular taxa de fluido é
 *     rotina diária. Era impossível pela interface.
 */

const CATEGORY_ORDER: PrescriptionCategory[] = [
  "medication",
  "fluids",
  "monitoring",
  "nutrition",
  "care",
  "procedure",
];

const HOUR_MS = 3_600_000;

/** O eixo cobre a mesma janela que o servidor devolve (`default_window`: 12h
 *  para trás, 12h para frente). Tarefa pendente mais velha que isso continua
 *  vindo (atrasada não expira) e ganha a coluna "antes" em vez de esticar a
 *  grade por dias. Some da grade seria repetir o erro clássico de eMAR que o
 *  backend documenta: a dose de anteontem sai da tela e fica no contador. */
const AXIS_BACK_MS = 12 * HOUR_MS;

const FREQUENCIES = [30, 60, 120, 240, 360, 480, 720, 1440];

/** Atalhos de "há quanto tempo" para o registro retroativo. */
const RETRO_MINUTES = [10, 20, 30, 45, 60, 90, 120];

interface ExecuteOptions {
  values?: Record<string, unknown>;
  retroactive?: boolean;
  performed_at?: string;
  partial?: boolean;
  confirm_early?: boolean;
}

/** Uma linha da grade. Nem toda linha tem prescrição ativa: ver `buildRows`. */
interface SheetRow {
  key: string;
  title: string;
  category: PrescriptionCategory;
  criticality: Criticality;
  prescription: Prescription | null;
  /** Tarefas cuja prescrição foi suspensa: o detalhe da internação as devolve,
   *  mas filtra a prescrição. Sem esta linha, o histórico sumiria da tela. */
  closed: boolean;
  tasks: Task[];
}

type Dialog =
  | { kind: "pin"; context?: string; retry: () => void }
  | { kind: "early"; task: Task; options: ExecuteOptions }
  | { kind: "notDone"; task: Task }
  | { kind: "taskMenu"; task: Task }
  | { kind: "partial"; task: Task }
  | { kind: "retroactive"; task: Task }
  | { kind: "prescription"; prescription: Prescription }
  | { kind: "adjust"; prescription: Prescription }
  | { kind: "prn"; prescription: Prescription };

/** Régua de horas no fuso da CLÍNICA.
 *
 *  `useClinic` formata instantes; a grade precisa de duas contas que não são
 *  formatação: em que hora da clínica cai a tarefa, e qual o instante em que
 *  essa hora começa. Sem fuso explícito, um quiosque em UTC colocaria a dose
 *  das 10h na coluna das 13h: o mesmo erro que a formatação já corrigiu,
 *  reaparecendo no eixo.
 *
 *  Este formatador é CHAVE, nunca rótulo: locale fixo e `h23` para a leitura
 *  ser determinística em qualquer navegador. Todo texto visível continua saindo
 *  de `useClinic()`, como o `dayKeyFmt` do próprio hook. */
function useClinicHours() {
  const { timezone } = useClinic();
  return useMemo(() => {
    const keyFmt = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: timezone,
    });
    /** "HH:MM" do relógio da clínica. Chave de agrupamento, não rótulo. */
    const hourKey = (ms: number) => keyFmt.format(new Date(ms));
    /** Instante em que começa a hora cheia da clínica que contém `ms`.
     *  Descontar os MINUTOS locais (e não truncar em UTC) mantém a coluna certa
     *  onde o fuso não é hora cheia: Índia (+5:30), Nepal (+5:45). */
    const slot = (ms: number) => {
      const minute = Number(hourKey(ms).slice(3));
      return Math.floor(ms / 60_000) * 60_000 - minute * 60_000;
    };
    return { hourKey, slot };
  }, [timezone]);
}

export function TreatmentSheet() {
  const { t } = useTranslation();
  const { detail, reload } = usePatientContext();
  const { time, timezone } = useClinic();
  const { hourKey, slot } = useClinicHours();
  const { can, needsOperator } = useSession();
  const describeError = useApiErrorMessage();

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);

  // A ficha fica aberta o turno inteiro num quiosque: sem o tique, o marcador
  // de "agora" congela no horário em que a página foi aberta e a grade passa a
  // mentir sobre o que já venceu.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const hospitalizationId = detail.hospitalization.id;

  const rows = useMemo(() => buildRows(detail.prescriptions, detail.tasks), [detail]);

  /** A grade começa AGORA.
   *
   *  A hora corrente encostada na borda esquerda é o que se lê primeiro, e é o
   *  que a pessoa foi ali fazer. Deixar o passado ocupando as primeiras colunas
   *  empurra o presente para fora da tela e obriga a rolar para trabalhar.
   *
   *  Mas o passado não pode simplesmente sumir: dose vencida escondida atrás da
   *  rolagem é o erro clássico de eMAR que a pesquisa nomeia (§4): a pessoa dá
   *  baixa no que vê, o contador não se move, e o sistema parece quebrado. Por
   *  isso o que ficou para trás vira uma coluna à esquerda que DIZ quantas são,
   *  e abre. */
  const [showPast, setShowPast] = useState(false);

  const axis = useMemo(() => {
    const nowSlot = slot(now);
    let first = nowSlot;
    let last = nowSlot;
    for (const task of detail.tasks) {
      const moment = slot(new Date(task.scheduled_for).getTime());
      if (moment < first) first = moment;
      if (moment > last) last = moment;
    }
    const start = showPast ? Math.max(first, nowSlot - AXIS_BACK_MS) : nowSlot;
    const hours: number[] = [];
    for (let moment = start; moment <= last; moment += HOUR_MS) hours.push(moment);
    // O que sobrou antes do eixo. Contamos só o que ainda EXIGE ação: uma dose
    // já dada às 14h não é dívida, é histórico.
    const behind = detail.tasks.filter(
      (task) => slot(new Date(task.scheduled_for).getTime()) < start,
    );
    return {
      hours,
      start,
      nowSlot,
      overflow: behind.length > 0,
      behind: behind.length,
      behindOpen: behind.filter((task) => task.status === "pending").length,
    };
  }, [detail.tasks, slot, now, showPast]);

  const groups = useMemo(() => groupByCategory(rows), [rows]);

  /** Abre um formulário que MUTA. Na estação sem PIN, pede a identificação
   *  antes: preencher a titulação inteira para levar `operator_required` no
   *  salvar é o caminho mais curto para a pessoa desistir e anotar no papel. */
  const open = useCallback(
    (next: Dialog, context?: string) => {
      if (needsOperator) {
        setDialog({ kind: "pin", context, retry: () => setDialog(next) });
        return;
      }
      setDialog(next);
    },
    [needsOperator],
  );

  const execute = useCallback(
    async (task: Task, options: ExecuteOptions = {}) => {
      setBusy(task.id);
      try {
        await api.executeTask(task.id, options);
        await reload();
        setError(null);
        setDialog(null);
      } catch (err) {
        if (err instanceof ApiError && err.code === "operator_required") {
          setDialog({
            kind: "pin",
            context: task.title,
            retry: () => void execute(task, options),
          });
          return;
        }
        if (err instanceof ApiError && err.code === "early_confirmation_required") {
          // Aviso, não bloqueio, e na língua do produto. Era um
          // `window.confirm()` do navegador no meio de uma interface desenhada.
          setDialog({ kind: "early", task, options });
          return;
        }
        setError(describeError(err));
      } finally {
        setBusy(null);
      }
    },
    [reload, describeError],
  );

  const suspend = useCallback(
    async (prescription: Prescription) => {
      setBusy(prescription.id);
      try {
        await api.suspendPrescription(prescription.id);
        await reload();
        setError(null);
        setDialog(null);
      } catch (err) {
        if (err instanceof ApiError && err.code === "operator_required") {
          setDialog({
            kind: "pin",
            context: prescription.name,
            retry: () => void suspend(prescription),
          });
          return;
        }
        setError(describeError(err));
      } finally {
        setBusy(null);
      }
    },
    [reload, describeError],
  );

  const closeDialog = useCallback(() => setDialog(null), []);
  const afterMutation = useCallback(async () => {
    setDialog(null);
    setError(null);
    await reload();
  }, [reload]);

  if (rows.length === 0) {
    return (
      <div className="sheet">
        {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}
        <EmptyState
          title={t("sheet.noPrescriptions")}
          hint={t("sheet.emptyHint")}
          action={
            <Gate can={CAN.prescriptionCreate}>
              <Link
                to={`/internacao/${hospitalizationId}/prescrever`}
                style={{ textDecoration: "none" }}
              >
                <Button>{t("sheet.newPrescription")}</Button>
              </Link>
            </Gate>
          }
        />
      </div>
    );
  }

  const columns = `var(--sheet-label) repeat(${axis.hours.length + (axis.overflow ? 1 : 0)}, var(--sheet-col))`;

  return (
    <div className="sheet">
      {/* A ficha não desenha "carregando": o esqueleto é do shell do paciente,
          que só monta esta aba com o detalhe já em mãos. */}
      {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}

      <Section title={t("sheet.gridTitle")} hint={t("sheet.gridHint", { timezone })}>
        {/* Quem não pode dar baixa via a grade inteira sem ação nenhuma e sem
            explicação: nenhuma dose oferecia o ✓, e a leitura natural era que
            a tela tinha quebrado. Esconder o botão está certo (a API recusaria
            de qualquer jeito); calar o motivo, não. O administrador não executa
            ato clínico de propósito. */}
        {!can(CAN.taskExecute) ? (
          <p className="sheet-readonly">{t("sheet.readOnlyForRole")}</p>
        ) : null}

        {axis.behindOpen > 0 && !showPast ? (
          <button type="button" className="sheet-behind-banner" onClick={() => setShowPast(true)}>
            <strong>{t("sheet.behindBanner", { count: axis.behindOpen })}</strong>
            <span>{t("sheet.behindBannerHint")}</span>
          </button>
        ) : null}
        <div className="sheet-scroll">
          <div className="sheet-grid" style={{ gridTemplateColumns: columns }}>
            <div className="sheet-cell sheet-head sheet-corner">
              <span className="sheet-axis-label">{t("sheet.axisLabel")}</span>
            </div>
            {axis.overflow ? (
              <button
                type="button"
                className={`sheet-cell sheet-head sheet-head-before${
                  axis.behindOpen > 0 ? " sheet-head-behind-late" : ""
                }`}
                onClick={() => setShowPast((v) => !v)}
                aria-expanded={showPast}
                title={t(showPast ? "sheet.hidePast" : "sheet.showPast")}
              >
                <span className="sheet-hour">{t("sheet.before")}</span>
                <span className="sheet-behind-count tabular">
                  {axis.behindOpen > 0
                    ? t("sheet.behindLate", { count: axis.behindOpen })
                    : t("sheet.behindDone", { count: axis.behind })}
                </span>
              </button>
            ) : null}
            {axis.hours.map((moment) => (
              <div
                key={moment}
                className={`sheet-cell sheet-head${moment === axis.nowSlot ? " sheet-now" : ""}${
                  moment < axis.nowSlot ? " sheet-past" : ""
                }${
                  // O eixo atravessa a meia-noite quase sempre (a janela é de
                  // 24h): sem a marca, "23:00 | 00:00" parece a mesma noite e
                  // uma dose de amanhã se lê como uma de hoje.
                  hourKey(moment).startsWith("00") ? " sheet-daybreak" : ""
                }`}
              >
                <span className="tabular sheet-hour">{time(new Date(moment))}</span>
                {moment === axis.nowSlot ? (
                  <span className="sheet-now-tag">{t("sheet.now")}</span>
                ) : null}
              </div>
            ))}

            {groups.map((group) => (
              <Fragment key={group.key}>
                <div className="sheet-group">
                  <span className="sheet-group-label">
                    {group.category
                      ? t(`prescription.category.${group.category}`)
                      : t("sheet.otherCategory")}
                  </span>
                </div>
                {group.rows.map((row) => (
                  <SheetRowView
                    key={row.key}
                    row={row}
                    axis={axis}
                    slot={slot}
                    hourKey={hourKey}
                    busy={busy}
                    canMenu={
                      row.prescription !== null &&
                      (can(CAN.prescriptionAdjust) ||
                        can(CAN.prescriptionSuspend) ||
                        (row.prescription.kind === "prn" && can(CAN.taskAdHoc)))
                    }
                    onExecute={(task) => void execute(task)}
                    onTaskMenu={(task) => open({ kind: "taskMenu", task }, task.title)}
                    onPrescriptionMenu={(prescription) =>
                      setDialog({ kind: "prescription", prescription })
                    }
                    onPrn={(prescription) => open({ kind: "prn", prescription }, prescription.name)}
                  />
                ))}
              </Fragment>
            ))}
          </div>
        </div>
      </Section>

      {dialog?.kind === "pin" ? (
        <PinDialog
          context={dialog.context}
          onDone={dialog.retry}
          onCancel={closeDialog}
        />
      ) : null}

      {dialog?.kind === "early" ? (
        <ConfirmEarly
          task={dialog.task}
          onCancel={closeDialog}
          onConfirm={() => void execute(dialog.task, { ...dialog.options, confirm_early: true })}
        />
      ) : null}

      {dialog?.kind === "notDone" ? (
        <NotDoneDialog
          task={dialog.task}
          onClose={closeDialog}
          onDone={afterMutation}
          onError={setError}
        />
      ) : null}

      {dialog?.kind === "taskMenu" ? (
        <TaskMenu
          task={dialog.task}
          onClose={closeDialog}
          onNotDone={() => setDialog({ kind: "notDone", task: dialog.task })}
          onPartial={() => setDialog({ kind: "partial", task: dialog.task })}
          onRetroactive={() => setDialog({ kind: "retroactive", task: dialog.task })}
        />
      ) : null}

      {dialog?.kind === "partial" ? (
        <PartialDialog
          task={dialog.task}
          busy={busy === dialog.task.id}
          onClose={closeDialog}
          onConfirm={(dose) =>
            void execute(dialog.task, { partial: true, values: { dose_given: dose } })
          }
        />
      ) : null}

      {dialog?.kind === "retroactive" ? (
        <RetroactiveDialog
          task={dialog.task}
          busy={busy === dialog.task.id}
          onClose={closeDialog}
          onConfirm={(performedAt) =>
            void execute(dialog.task, { retroactive: true, performed_at: performedAt })
          }
        />
      ) : null}

      {dialog?.kind === "prescription" ? (
        <PrescriptionMenu
          prescription={dialog.prescription}
          busy={busy === dialog.prescription.id}
          onClose={closeDialog}
          onPrn={() => open({ kind: "prn", prescription: dialog.prescription }, dialog.prescription.name)}
          onAdjust={() =>
            open({ kind: "adjust", prescription: dialog.prescription }, dialog.prescription.name)
          }
          onSuspend={() => void suspend(dialog.prescription)}
        />
      ) : null}

      {dialog?.kind === "adjust" ? (
        <AdjustDialog
          prescription={dialog.prescription}
          onClose={closeDialog}
          onDone={afterMutation}
        />
      ) : null}

      {dialog?.kind === "prn" ? (
        <PrnDialog
          prescription={dialog.prescription}
          onClose={closeDialog}
          onDone={afterMutation}
        />
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Montagem das linhas
 * ---------------------------------------------------------------------- */

/** Linhas da grade a partir das prescrições ATIVAS e de todas as tarefas.
 *
 *  `GET /hospitalizations/{id}` filtra prescrições suspensas mas devolve as
 *  tarefas delas. Descartar a sobra faria um fármaco suspenso (e todo o
 *  histórico de execução dele) desaparecer da ficha sem aviso, que é
 *  exatamente o registro que a auditoria precisa enxergar. Não dá para mudar o
 *  backend, então a sobra vira uma linha rotulada. */
function buildRows(prescriptions: Prescription[], tasks: Task[]): SheetRow[] {
  const grouped = new Map<string, Task[]>();
  for (const task of tasks) {
    // Tarefa avulsa (ad-hoc sem prescrição) só tem título: agrupa por ele para
    // não virar uma linha por execução.
    const key = task.prescription_id ?? `ad-hoc:${task.category}:${task.title}`;
    const bucket = grouped.get(key);
    if (bucket) bucket.push(task);
    else grouped.set(key, [task]);
  }

  const rows: SheetRow[] = [];
  for (const prescription of prescriptions) {
    rows.push({
      key: prescription.id,
      title: prescription.name,
      category: prescription.category,
      criticality: prescription.criticality,
      prescription,
      closed: false,
      tasks: grouped.get(prescription.id) ?? [],
    });
    grouped.delete(prescription.id);
  }

  for (const [key, bucket] of grouped) {
    const first = bucket[0];
    rows.push({
      key,
      title: first.title,
      category: first.category,
      criticality: first.criticality,
      prescription: null,
      closed: first.prescription_id !== null,
      tasks: bucket,
    });
  }
  return rows;
}

function groupByCategory(rows: SheetRow[]) {
  const groups = CATEGORY_ORDER.map((category) => ({
    key: category as string,
    category: category as PrescriptionCategory | null,
    rows: rows.filter((row) => row.category === category),
  })).filter((group) => group.rows.length > 0);

  // A categoria vem da API como string: uma categoria nova no servidor não pode
  // fazer a linha sumir da ficha em silêncio.
  const known = new Set<string>(CATEGORY_ORDER);
  const rest = rows.filter((row) => !known.has(row.category));
  if (rest.length > 0) groups.push({ key: "__other__", category: null, rows: rest });
  return groups;
}

/* -------------------------------------------------------------------------
 * Linha e célula
 * ---------------------------------------------------------------------- */

interface Axis {
  hours: number[];
  start: number;
  nowSlot: number;
  overflow: boolean;
}

function SheetRowView({
  row,
  axis,
  slot,
  hourKey,
  busy,
  canMenu,
  onExecute,
  onTaskMenu,
  onPrescriptionMenu,
  onPrn,
}: {
  row: SheetRow;
  axis: Axis;
  slot: (ms: number) => number;
  hourKey: (ms: number) => string;
  busy: string | null;
  canMenu: boolean;
  onExecute: (task: Task) => void;
  onTaskMenu: (task: Task) => void;
  onPrescriptionMenu: (prescription: Prescription) => void;
  onPrn: (prescription: Prescription) => void;
}) {
  const { t } = useTranslation();
  const { time, duration } = useClinic();

  const placed = useMemo(() => {
    const before: Task[] = [];
    const cells: Task[][] = axis.hours.map(() => []);
    for (const task of row.tasks) {
      const index = Math.round(
        (slot(new Date(task.scheduled_for).getTime()) - axis.start) / HOUR_MS,
      );
      // Fora do eixo só acontece antes do corte de 12h, e só então existe a
      // coluna "antes". Os outros ramos são inalcançáveis pela construção do
      // eixo, mas somem para a borda mais próxima em vez de perder a tarefa em
      // silêncio, que é o que a grade nunca pode fazer.
      if (index < 0) (axis.overflow ? before : cells[0]).push(task);
      else if (index >= cells.length) cells[cells.length - 1].push(task);
      else cells[index].push(task);
    }
    return { before, cells };
  }, [row.tasks, axis, slot]);

  /** Os horários CONCRETOS da prescrição: "q8h · 10:00 / 18:00 / 02:00".
   *
   *  "a cada 8h" sozinho não diz quando. A pesquisa aponta SID/BID/TID
   *  ambíguos como fonte documentada de sobredose: a ficha tem de mostrar a
   *  âncora, não só a cadência. */
  const anchors = useMemo(() => {
    const seen = new Map<string, number>();
    for (const task of row.tasks) {
      const ms = new Date(task.scheduled_for).getTime();
      const key = hourKey(ms);
      if (!seen.has(key)) seen.set(key, ms);
    }
    return [...seen.entries()]
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([, ms]) => time(new Date(ms)));
  }, [row.tasks, hourKey, time]);

  const prescription = row.prescription;
  const schedule: string[] = [];
  if (prescription) {
    if (prescription.kind === "prn") {
      schedule.push(t("sheet.prn"));
      if (prescription.min_interval_minutes) {
        schedule.push(
          t("sheet.prnMinInterval", { interval: duration(prescription.min_interval_minutes) }),
        );
      }
      if (prescription.max_doses_24h) {
        schedule.push(t("sheet.prnMaxDoses", { n: prescription.max_doses_24h }));
      }
    } else {
      const minutes = prescription.frequency_minutes ?? 0;
      schedule.push(
        minutes % 60 === 0
          ? t("sheet.frequency", { hours: minutes / 60 })
          : t("sheet.frequencyMinutes", { minutes }),
      );
      // Só quando cabem TODAS. Cortar a lista em seis diria "é nestas horas" a
      // respeito de uma prescrição de 30 em 30 minutos. Pior que não dizer.
      if (anchors.length > 0 && anchors.length <= 6) {
        schedule.push(t("sheet.doseTimes", { times: anchors.join(" / ") }));
      }
    }
    if (prescription.kind === "continuous") {
      const rate = prescription.details?.rate_ml_h;
      schedule.push(
        typeof rate === "number" || typeof rate === "string"
          ? t("sheet.rate", { rate })
          : t("sheet.continuous"),
      );
    }
  } else {
    schedule.push(row.closed ? t("sheet.closedHint") : t("sheet.adHocHint"));
  }

  return (
    <>
      <div className="sheet-cell sheet-label">
        <div className="sheet-label-head">
          <strong className="sheet-rx-name">{row.title}</strong>
          {canMenu && prescription ? (
            <button
              type="button"
              className="sheet-menu"
              onClick={() => onPrescriptionMenu(prescription)}
              aria-label={t("sheet.actionsFor", { name: row.title })}
              title={t("sheet.actions")}
            >
              ⋯
            </button>
          ) : null}
        </div>
        <div className="sheet-badges">
          {row.criticality === "critical" ? (
            <Badge tone="late">{t("sheet.criticality.critical")}</Badge>
          ) : null}
          {prescription?.is_controlled ? <Badge tone="warn">{t("sheet.controlled")}</Badge> : null}
          {row.closed ? <Badge>{t("sheet.closed")}</Badge> : null}
          {!prescription && !row.closed ? <Badge>{t("sheet.adHoc")}</Badge> : null}
        </div>
        <div className="sheet-rx-meta">{schedule.join(" · ")}</div>
        {/* PRN não tem agenda: sem esta ação a linha ficava com um "–" e nenhum
            caminho para registrar a dose que acabou de ser dada. */}
        {prescription?.kind === "prn" ? (
          <Gate can={CAN.taskAdHoc}>
            <button type="button" className="sheet-prn" onClick={() => onPrn(prescription)}>
              {t("sheet.registerDose")}
            </button>
          </Gate>
        ) : null}
      </div>

      {axis.overflow ? (
        <div className="sheet-cell sheet-body sheet-past">
          {placed.before.map((task) => (
            <TaskCell
              key={task.id}
              task={task}
              busy={busy === task.id}
              onExecute={onExecute}
              onMenu={onTaskMenu}
            />
          ))}
        </div>
      ) : null}

      {placed.cells.map((tasks, index) => {
        const moment = axis.hours[index];
        const classes = ["sheet-cell", "sheet-body"];
        if (moment === axis.nowSlot) classes.push("sheet-now");
        else if (moment < axis.nowSlot) classes.push("sheet-past");
        if (hourKey(moment).startsWith("00")) classes.push("sheet-daybreak");
        return (
          <div key={moment} className={classes.join(" ")}>
            {tasks.map((task) => (
              <TaskCell
                key={task.id}
                task={task}
                busy={busy === task.id}
                onExecute={onExecute}
                onMenu={onTaskMenu}
              />
            ))}
          </div>
        );
      })}
    </>
  );
}

/** A célula: um fato, não um chip decorativo.
 *
 *  Pendente mostra a hora prevista e as duas saídas. Feita mostra QUANDO foi
 *  feita de verdade. Não feita mostra o MOTIVO, riscado: a marca de que
 *  aquela dose não entrou no paciente. `early` e `retroactive` são gravados
 *  pelo servidor desde sempre e nenhuma tela os exibia: uma dose adiantada
 *  ficava indistinguível de uma dose no horário. */
function TaskCell({
  task,
  busy,
  onExecute,
  onMenu,
}: {
  task: Task;
  busy: boolean;
  onExecute: (task: Task) => void;
  onMenu: (task: Task) => void;
}) {
  const { t } = useTranslation();
  const { moment } = useClinic();
  const colors = stateColors(task.display_state);
  const pending = task.status === "pending";
  const executed = task.status === "done" || task.status === "partial";
  const dose = task.values?.dose_given;

  return (
    <div
      className="cell-task"
      style={{ borderColor: colors.edge, background: colors.bg, color: colors.fg }}
    >
      <span className="cell-line">
        {/* `moment` e não `time`: a dose de anteontem que continua pendente cai
            na coluna "antes" e, escrita só como "10:00", se confunde com a de
            hoje: a pessoa vê duas linhas iguais e conclui que a baixa falhou. */}
        <span className="tabular cell-time">{moment(task.scheduled_for)}</span>
        {executed ? <span aria-hidden="true">✓</span> : null}
      </span>

      {executed && task.executed_at ? (
        <span className="tabular cell-real">
          {t("sheet.executedAt", { time: moment(task.executed_at) })}
        </span>
      ) : null}

      {task.status === "not_done" ? (
        <span className="cell-reason">
          {t(`task.reason.${task.outcome_reason ?? "other"}`)}
        </span>
      ) : null}

      {dose !== undefined && dose !== null ? (
        <span className="cell-dose">{t("sheet.doseGiven", { dose: String(dose) })}</span>
      ) : null}

      {task.status === "partial" || task.early || task.retroactive ? (
        <span className="cell-flags">
          {task.status === "partial" ? (
            <span className="cell-flag">{t("task.flag.partial")}</span>
          ) : null}
          {task.early ? <span className="cell-flag">{t("task.flag.early")}</span> : null}
          {task.retroactive ? <span className="cell-flag">{t("task.flag.retroactive")}</span> : null}
        </span>
      ) : null}

      {pending ? (
        <span className="cell-actions">
          <Gate can={CAN.taskExecute}>
            <button
              type="button"
              className="cell-action"
              disabled={busy}
              onClick={() => onExecute(task)}
              aria-label={`${t("task.execute")} · ${task.title}`}
              title={t("task.execute")}
            >
              ✓
            </button>
            <button
              type="button"
              className="cell-action"
              disabled={busy}
              onClick={() => onMenu(task)}
              aria-label={`${t("sheet.actions")} · ${task.title}`}
              title={t("sheet.actions")}
            >
              ⋯
            </button>
          </Gate>
        </span>
      ) : null}
    </div>
  );
}

/* -------------------------------------------------------------------------
 * Diálogos
 *
 * Todos são modais do produto. Popover ancorado seria recortado: a grade rola
 * dentro do próprio contêiner, e `overflow-x: auto` corta o que sai dele.
 * ---------------------------------------------------------------------- */

/** Dose adiantada: aviso auditado, nunca bloqueio.
 *
 *  Cópia local de propósito: a mesma pergunta aparece no console do plantão,
 *  e acoplar as duas telas por um import faria uma mudar quando a outra
 *  mudasse. A janela ISMP vale nos dois lados: adiantar é erro como atrasar. */
function ConfirmEarly({
  task,
  onConfirm,
  onCancel,
}: {
  task: Task;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const { moment } = useClinic();
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{task.title}</h2>
        <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 14 }}>
          {t("task.confirmEarly", { time: moment(task.scheduled_for) })}
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

/** As saídas menos comuns de uma tarefa, fora do caminho da baixa simples. */
function TaskMenu({
  task,
  onClose,
  onNotDone,
  onPartial,
  onRetroactive,
}: {
  task: Task;
  onClose: () => void;
  onNotDone: () => void;
  onPartial: () => void;
  onRetroactive: () => void;
}) {
  const { t } = useTranslation();
  const { moment } = useClinic();
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{task.title}</h2>
        <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-3)" }}>
          {moment(task.scheduled_for)}
        </p>
        <div className="menu-list">
          <MenuItem title={t("task.notDone")} hint={t("sheet.notDoneHint")} onClick={onNotDone} />
          <MenuItem
            title={t("sheet.partial")}
            hint={t("sheet.partialHint")}
            onClick={onPartial}
          />
          <MenuItem
            title={t("sheet.retroactive")}
            hint={t("sheet.retroactiveHint")}
            onClick={onRetroactive}
          />
        </div>
        <Button variant="secondary" onClick={onClose}>
          {t("common.cancel")}
        </Button>
      </div>
    </div>
  );
}

function MenuItem({
  title,
  hint,
  onClick,
  tone = "neutral",
}: {
  title: string;
  hint: string;
  onClick: () => void;
  tone?: "neutral" | "danger";
}) {
  return (
    <button type="button" className={`menu-item menu-item-${tone}`} onClick={onClick}>
      <strong>{title}</strong>
      <span>{hint}</span>
    </button>
  );
}

/** Execução parcial: metade da dose vomitada é rotina e não havia como
 *  registrar. Sem isto, a escolha era mentir "feita" ou mentir "não feita". */
function PartialDialog({
  task,
  busy,
  onClose,
  onConfirm,
}: {
  task: Task;
  busy: boolean;
  onClose: () => void;
  onConfirm: (dose: string) => void;
}) {
  const { t } = useTranslation();
  const [dose, setDose] = useState("");
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{t("sheet.partial")}</h2>
        <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{task.title}</p>
        <Field label={t("sheet.doseField")}>
          <input
            style={inputStyle}
            value={dose}
            onChange={(event) => setDose(event.target.value)}
            placeholder={t("sheet.doseFieldHint")}
          />
        </Field>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          {/* O servidor exige `values.dose_given`: sem a dose, "parcial" não
              informa nada a quem ler o registro depois. */}
          <Button disabled={busy || dose.trim() === ""} onClick={() => onConfirm(dose.trim())}>
            {t("sheet.registerPartial")}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Registro retroativo: fez agora, documenta depois.
 *
 *  Pergunta HÁ QUANTO TEMPO, não a hora absoluta. Um `datetime-local` é lido no
 *  fuso do APARELHO: num quiosque em UTC a pessoa digitaria 10:00 e gravaria
 *  13:00 da clínica, justamente na tela cujo trabalho é a hora certa. Duração é
 *  imune a fuso, e é assim que a emergência se lembra do fato: "isso foi há uns
 *  vinte minutos". */
function RetroactiveDialog({
  task,
  busy,
  onClose,
  onConfirm,
}: {
  task: Task;
  busy: boolean;
  onClose: () => void;
  onConfirm: (performedAt: string) => void;
}) {
  const { t } = useTranslation();
  const { moment, duration } = useClinic();
  const [minutes, setMinutes] = useState(20);
  const performedAt = new Date(Date.now() - minutes * 60_000);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{t("sheet.retroactive")}</h2>
        <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{task.title}</p>

        <Field label={t("sheet.howLongAgo")}>
          <div className="chip-group">
            {RETRO_MINUTES.map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={minutes === option}
                className={minutes === option ? "chip chip-on" : "chip"}
                onClick={() => setMinutes(option)}
              >
                {duration(option)}
              </button>
            ))}
          </div>
        </Field>

        <Field label={t("sheet.minutesAgo")}>
          <input
            style={inputStyle}
            type="number"
            min={1}
            max={1440}
            value={minutes}
            onChange={(event) => setMinutes(Math.max(1, Number(event.target.value) || 1))}
          />
        </Field>

        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
          {t("sheet.retroactiveResult", { time: moment(performedAt) })}
        </p>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          {/* O instante é calculado no CLIQUE, não no render: o diálogo pode
              ficar aberto alguns minutos e gravaria uma hora já vencida. */}
          <Button
            disabled={busy}
            onClick={() => onConfirm(new Date(Date.now() - minutes * 60_000).toISOString())}
          >
            {t("sheet.registerRetroactive")}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** As ações da prescrição num lugar só: seis botões por linha destruiriam a
 *  legibilidade da grade, que é a razão de ela existir. */
function PrescriptionMenu({
  prescription,
  busy,
  onClose,
  onPrn,
  onAdjust,
  onSuspend,
}: {
  prescription: Prescription;
  busy: boolean;
  onClose: () => void;
  onPrn: () => void;
  onAdjust: () => void;
  onSuspend: () => void;
}) {
  const { t } = useTranslation();
  const [confirmSuspend, setConfirmSuspend] = useState(false);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{prescription.name}</h2>

        {confirmSuspend ? (
          <>
            <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>
              {t("sheet.suspendConfirm")}
            </p>
            <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
              {t("sheet.suspendHint")}
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <Button variant="secondary" onClick={() => setConfirmSuspend(false)}>
                {t("common.back")}
              </Button>
              <Button variant="danger" disabled={busy} onClick={onSuspend}>
                {t("prescription.suspend")}
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="menu-list">
              {prescription.kind === "prn" ? (
                <Gate can={CAN.taskAdHoc}>
                  <MenuItem
                    title={t("sheet.registerDose")}
                    hint={t("sheet.registerDoseHint")}
                    onClick={onPrn}
                  />
                </Gate>
              ) : null}
              <Gate can={CAN.prescriptionAdjust}>
                <MenuItem
                  title={t("sheet.adjust")}
                  hint={t("sheet.adjustHint")}
                  onClick={onAdjust}
                />
              </Gate>
              <Gate can={CAN.prescriptionSuspend}>
                <MenuItem
                  title={t("prescription.suspend")}
                  hint={t("sheet.suspendMenuHint")}
                  tone="danger"
                  onClick={() => setConfirmSuspend(true)}
                />
              </Gate>
            </div>
            <Button variant="secondary" onClick={onClose}>
              {t("common.cancel")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

/** Titulação. Cria uma VERSÃO nova ligada à anterior por
 *  `replaces_prescription_id`: o servidor suspende a antiga, cancela as
 *  pendentes e apraza a nova. Ajustar taxa de fluido é rotina diária e não
 *  havia caminho nenhum na interface; suspender e prescrever de novo quebraria
 *  a cronologia da prescrição na auditoria. */
function AdjustDialog({
  prescription,
  onClose,
  onDone,
}: {
  prescription: Prescription;
  onClose: () => void;
  onDone: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();

  const rateBefore =
    prescription.details?.rate_ml_h === undefined || prescription.details?.rate_ml_h === null
      ? ""
      : String(prescription.details.rate_ml_h);

  const [name, setName] = useState(prescription.name);
  const [frequency, setFrequency] = useState(prescription.frequency_minutes ?? 0);
  const [criticality, setCriticality] = useState<Criticality>(prescription.criticality);
  const [rate, setRate] = useState(rateBefore);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const changes: Record<string, unknown> = {};
  if (name.trim() !== "" && name !== prescription.name) changes.name = name.trim();
  if (prescription.kind !== "prn" && frequency !== prescription.frequency_minutes) {
    changes.frequency_minutes = frequency;
  }
  if (criticality !== prescription.criticality) changes.criticality = criticality;
  if (prescription.kind === "continuous" && rate !== rateBefore && rate.trim() !== "") {
    // `details` é substituído inteiro pelo servidor: mandar só a taxa apagaria
    // o resto do que o vet escreveu ao prescrever.
    changes.details = { ...prescription.details, rate_ml_h: Number(rate) };
  }
  const nothingChanged = Object.keys(changes).length === 0;

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.adjustPrescription(prescription.id, { ...changes, reason: reason.trim() });
      await onDone();
    } catch (err) {
      // Inclusive `operator_required`: a mensagem traduzida pede o PIN e o
      // formulário continua preenchido, em vez de perder o que foi digitado.
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{t("sheet.adjust")}</h2>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>{t("sheet.adjustVersion")}</p>

        <ErrorBanner message={error} />

        <Field label={t("prescription.name")}>
          <input
            style={inputStyle}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </Field>

        {prescription.kind === "continuous" ? (
          <Field label={t("prescription.rate")}>
            <input
              style={inputStyle}
              type="number"
              min={1}
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          </Field>
        ) : null}

        {prescription.kind !== "prn" ? (
          <Field label={t("prescription.frequency")}>
            <select
              style={inputStyle}
              value={String(frequency)}
              onChange={(event) => setFrequency(Number(event.target.value))}
            >
              {FREQUENCIES.map((minutes) => (
                <option key={minutes} value={minutes}>
                  {minutes % 60 === 0
                    ? t("sheet.frequency", { hours: minutes / 60 })
                    : t("sheet.frequencyMinutes", { minutes })}
                </option>
              ))}
            </select>
          </Field>
        ) : null}

        <Field label={t("prescription.criticality")}>
          <div className="chip-group">
            {(["normal", "critical"] as const).map((option) => (
              <button
                key={option}
                type="button"
                aria-pressed={criticality === option}
                className={criticality === option ? "chip chip-on" : "chip"}
                onClick={() => setCriticality(option)}
              >
                {t(`prescription.criticality.${option}`)}
              </button>
            ))}
          </div>
        </Field>

        <Field label={t("sheet.adjustReason")}>
          <input
            style={inputStyle}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder={t("sheet.adjustReasonHint")}
          />
        </Field>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          <Button disabled={busy || nothingChanged || reason.trim() === ""} onClick={() => void submit()}>
            {t("sheet.adjustSubmit")}
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Dose PRN ("se necessário"): a tarefa nasce avulsa, já executada.
 *
 *  O guard-rail do servidor (intervalo mínimo, teto de doses em 24h) responde
 *  409 e é AVISO COM SOBREPOSIÇÃO AUDITADA, nunca bloqueio: fricção produz
 *  workaround que falsifica o registro: a dose sai do armário e não aparece em
 *  lugar nenhum. Quem decide é o profissional; o sistema registra a decisão. */
function PrnDialog({
  prescription,
  onClose,
  onDone,
}: {
  prescription: Prescription;
  onClose: () => void;
  onDone: () => void | Promise<void>;
}) {
  const { t } = useTranslation();
  const { duration } = useClinic();
  const describeError = useApiErrorMessage();

  const [dose, setDose] = useState("");
  const [guardrail, setGuardrail] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(override: boolean) {
    setBusy(true);
    setError(null);
    try {
      await api.adHocTask({
        prescription_id: prescription.id,
        values: dose.trim() === "" ? undefined : { dose_given: dose.trim() },
        override,
      });
      await onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "prn_guardrail") {
        setGuardrail(err.params);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  const rule = typeof guardrail?.rule === "string" ? guardrail.rule : null;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card">
        <h2 style={{ fontSize: 17 }}>{t("sheet.registerDose")}</h2>
        <p style={{ margin: 0, fontSize: 14, color: "var(--ink-2)" }}>{prescription.name}</p>

        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-3)" }}>
          {[
            prescription.min_interval_minutes
              ? t("sheet.prnMinInterval", { interval: duration(prescription.min_interval_minutes) })
              : null,
            prescription.max_doses_24h
              ? t("sheet.prnMaxDoses", { n: prescription.max_doses_24h })
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>

        <ErrorBanner message={error} />

        {rule ? (
          <div className="prn-guardrail" role="alert">
            <strong>{t("sheet.prnGuardrailTitle")}</strong>
            <span>{t(`sheet.prnGuardrail.${rule}`, { ...guardrail })}</span>
            <span className="prn-guardrail-hint">{t("sheet.prnGuardrailHint")}</span>
          </div>
        ) : null}

        <Field label={t("sheet.doseField")}>
          <input
            style={inputStyle}
            value={dose}
            onChange={(event) => setDose(event.target.value)}
            placeholder={t("sheet.doseFieldHint")}
          />
        </Field>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="secondary" onClick={onClose}>
            {t("common.cancel")}
          </Button>
          {rule ? (
            <Button variant="danger" disabled={busy} onClick={() => void submit(true)}>
              {t("task.executeAnyway")}
            </Button>
          ) : (
            <Button disabled={busy} onClick={() => void submit(false)}>
              {t("sheet.registerDose")}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
