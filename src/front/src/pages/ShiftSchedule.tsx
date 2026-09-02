import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { api, asList } from "../api/client";
import type { MembershipRow, Shift } from "../api/types";
import { CAN } from "../api/capabilities";
import { AdminNote, CheckRow, initials, license, usePinRetry } from "../components/AdminShared";
import { Gate } from "../components/authz";
import { Combobox } from "../components/Combobox";
import {
  Button,
  Card,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import "../styles/admin.css";
import "../styles/agenda.css";

/** A escala do plantão.
 *
 *  **O objeto é o TURNO, e ele tem uma equipe.** A tela tratava o objeto
 *  errado: para pôr a segunda pessoa no plantão da noite era preciso preencher
 *  de novo nome, início e fim, exatamente iguais. Alguns segundos de diferença
 *  na digitação viravam um turno separado, e a clínica de demonstração acabou
 *  com quatro "Diurno" começando às 11:08, 11:11 e 11:16. Não era erro de quem
 *  digitou: era a interface pedindo para descrever o turno uma vez por pessoa.
 *
 *  Agora o gesto principal é apontar. Clicar num turno abre a equipe dele, com
 *  "adicionar alguém" ali dentro, sem redigitar nada. Clicar num espaço vazio
 *  cria um turno, e o sistema já oferece os turnos que ESTA clínica usa, com o
 *  horário que ela mais usa: quem monta a escala escolhe entre "Diurno" e
 *  "Noturno" em vez de descrever duas datas do zero toda vez.
 *
 *  A agenda, por sua vez, existe porque a lista de cartões escondia a única
 *  coisa que a escala precisa responder: quando há gente e quando não há. Um
 *  buraco de duas horas não ocupava espaço nenhum, e três turnos sobrepostos
 *  eram três cartões iguais lado a lado. Aqui o tempo é o eixo, o turno que
 *  atravessa a meia-noite aparece nos dois dias, e uma linha marca o agora.
 */
const DAY_MS = 86_400_000;
const MINUTE_MS = 60_000;
/** Altura de uma hora, em pixels. Um turno de 12h vira um bloco de 384px, que
 *  cabe o nome, as pessoas e o selo sem apertar. */
const HOUR_PX = 32;
const DAYS_IN_WEEK = 7;

/** Deslocamento do fuso NESTE instante, em milissegundos. */
function zoneOffsetMs(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const at = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? "0");
  const wall = Date.UTC(
    at("year"),
    at("month") - 1,
    at("day"),
    at("hour"),
    at("minute"),
    at("second"),
  );
  return wall - (instant.getTime() - instant.getMilliseconds());
}

/** "2026-09-01T22:00" digitado no formulário → instante UTC, lendo os campos
 *  como o relógio da CLÍNICA.
 *
 *  `new Date("2026-09-01T22:00")` lê no fuso do APARELHO: um administrador em
 *  Lisboa escalava o turno das 22h de São Paulo para as 18h. É o mesmo erro que
 *  fazia o quiosque em UTC mostrar a dose das 10h como 13h, só que na escrita,
 *  e aqui ele fica gravado. */
function clinicIso(local: string, timeZone: string): string | null {
  if (!local) return null;
  const wall = new Date(`${local}:00Z`);
  if (Number.isNaN(wall.getTime())) return null;
  // Duas passadas: a primeira usa o deslocamento do palpite, a segunda o do
  // instante achado: é o que acerta a virada do horário de verão.
  const first = new Date(wall.getTime() - zoneOffsetMs(wall, timeZone));
  const second = new Date(wall.getTime() - zoneOffsetMs(first, timeZone));
  return second.toISOString();
}

/** O caminho de volta: um Date cujos campos UTC valem como o relógio da
 *  CLÍNICA. É o que permite perguntar "que horas são neste turno" sem que a
 *  resposta dependa de onde está quem olha. */
function clinicWall(value: string | Date, timeZone: string): Date {
  const instant = value instanceof Date ? value : new Date(value);
  return new Date(instant.getTime() + zoneOffsetMs(instant, timeZone));
}

/** "2026-09-01" da data-parede. É a chave do dia na agenda. */
function dayKey(wall: Date): string {
  return wall.toISOString().slice(0, 10);
}

/** Minutos desde a meia-noite → "07:00". */
function hhmm(minutes: number): string {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** O texto que o `datetime-local` entende, a partir de uma data-parede. */
function localInput(wall: Date): string {
  return wall.toISOString().slice(0, 16);
}

/** Um turno que a clínica já usa, com o horário que ela mais usa.
 *
 *  Não é configuração nova: sai dos turnos que já existem. Quem monta a escala
 *  toma UMA decisão por semana ("quem cobre a noite de quinta"), e a tela
 *  pedia quatro campos por pessoa por dia. O nome e as horas do plantão da
 *  clínica não mudam toda semana, então o sistema não devia perguntar. */
interface ShiftTemplate {
  name: string;
  /** Minutos desde a meia-noite. O fim pode ser menor que o início: é o turno
   *  que atravessa a madrugada, e ele termina no dia seguinte. */
  from: number;
  to: number;
  uses: number;
}

/** Um pedaço de turno dentro de UM dia.
 *
 *  Um turno das 20h às 8h não é um retângulo: são dois, um em cada dia. Quem
 *  trabalha à noite lê a própria escala assim, e desenhar só no dia de início
 *  deixaria a manhã seguinte parecendo descoberta. */
interface Segment {
  key: string;
  slotKey: string;
  name: string;
  shifts: Shift[];
  /** Minutos desde a meia-noite da clínica. */
  from: number;
  to: number;
  hasVet: boolean;
  running: boolean;
  closed: boolean;
  /** Início e fim reais do turno, para o rótulo e o painel. */
  startsAt: string;
  endsAt: string;
  /** Coluna dentro do dia, quando há turnos sobrepostos. */
  lane: number;
  lanes: number;
}

export function ShiftSchedule() {
  const { t } = useTranslation();
  const { run, dialog, error, busy, describeError } = usePinRetry();
  const clinic = useClinic();

  const [shifts, setShifts] = useState<Shift[] | null>(null);
  const [shiftsError, setShiftsError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [members, setMembers] = useState<MembershipRow[] | null>(null);
  const [membersError, setMembersError] = useState<string | null>(null);

  /** Domingo da semana visível, em data-parede da clínica. */
  const [weekStart, setWeekStart] = useState<Date>(() => new Date());
  const [selected, setSelected] = useState<string | null>(null);

  /** O turno sendo criado. `null` = ninguém apontou nada, e a tela não
   *  pergunta: um formulário de quatro campos parado o tempo todo é a tela
   *  perguntando antes de alguém ter algo a responder. */
  const [draft, setDraft] = useState<{ day: Date | null } | null>(null);
  /** Quem está sendo somado ao turno aberto. */
  const [adding, setAdding] = useState(false);
  const [addMember, setAddMember] = useState("");

  const [shiftName, setShiftName] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [shiftMember, setShiftMember] = useState("");
  const [vetResponsible, setVetResponsible] = useState(false);
  /** Só quando a clínica não tem um turno parecido, ou quando quem monta pede
   *  "outro horário": os campos de data são a saída, não a porta de entrada. */
  const [manualTime, setManualTime] = useState(false);

  const timezone = clinic.timezone;

  /** O domingo da semana que contém `weekStart`, à meia-noite da clínica. */
  const sunday = useMemo(() => {
    const wall = clinicWall(weekStart, timezone);
    const base = new Date(
      Date.UTC(wall.getUTCFullYear(), wall.getUTCMonth(), wall.getUTCDate()),
    );
    base.setUTCDate(base.getUTCDate() - base.getUTCDay());
    return base;
  }, [weekStart, timezone]);

  const days = useMemo(
    () =>
      Array.from({ length: DAYS_IN_WEEK }, (_, index) => {
        const day = new Date(sunday.getTime() + index * DAY_MS);
        return { key: dayKey(day), wall: day };
      }),
    [sunday],
  );

  const loadShifts = useCallback(async () => {
    setShiftsError(null);
    try {
      // A janela acompanha a semana visível, com um dia de folga dos dois
      // lados para o turno que atravessa a meia-noite não sumir na borda.
      const from = new Date(sunday.getTime() - DAY_MS);
      const to = new Date(sunday.getTime() + (DAYS_IN_WEEK + 1) * DAY_MS);
      const page = await api.shifts({
        from: new Date(from.getTime() - zoneOffsetMs(from, timezone)).toISOString(),
        to: new Date(to.getTime() - zoneOffsetMs(to, timezone)).toISOString(),
        // O teto da API é 200 (`MAX_LIMIT`): pedir mais devolve 422, e a
        // agenda ficava em branco com "confira os campos preenchidos". Uma
        // semana de turnos cabe com folga.
        limit: 200,
      });
      setShifts(asList(page));
    } catch (err) {
      setShiftsError(describeError(err));
    }
  }, [sunday, timezone, describeError]);

  const loadMembers = useCallback(async () => {
    setMembersError(null);
    try {
      setMembers(asList(await api.memberships()));
    } catch (err) {
      setMembersError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void loadShifts();
  }, [loadShifts]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  const memberById = useMemo(() => {
    const map = new Map<string, MembershipRow>();
    for (const member of members ?? []) map.set(member.id, member);
    return map;
  }, [members]);

  const activeMembers = useMemo(
    () => (members ?? []).filter((member) => member.is_active),
    [members],
  );

  /** Turnos idênticos (mesmo nome, mesmo início, mesmo fim) são UM bloco com
   *  várias pessoas. Dois cartões iguais lado a lado não são dois turnos. */
  const slots = useMemo(() => {
    const map = new Map<string, { name: string; shifts: Shift[]; hasVet: boolean }>();
    for (const shift of shifts ?? []) {
      const key = `${shift.name}|${shift.starts_at}|${shift.ends_at}`;
      const slot = map.get(key) ?? { name: shift.name, shifts: [], hasVet: false };
      slot.shifts.push(shift);
      slot.hasVet = slot.hasVet || shift.is_vet_responsible;
      map.set(key, slot);
    }
    return map;
  }, [shifts]);

  /** Os retângulos da agenda, por dia, já com as faixas de sobreposição. */
  const byDay = useMemo(() => {
    const now = Date.now();
    const mapa = new Map<string, Segment[]>();
    for (const [slotKey, slot] of slots) {
      const primeiro = slot.shifts[0];
      const inicio = clinicWall(primeiro.starts_at, timezone);
      const fim = clinicWall(primeiro.ends_at, timezone);
      const running =
        Date.parse(primeiro.starts_at) <= now && now < Date.parse(primeiro.ends_at);
      const closed = slot.shifts.every((shift) => shift.closed_at !== null);

      // Um dia por vez, do dia do início ao dia do fim: é assim que o turno da
      // madrugada aparece na manhã seguinte em vez de sumir.
      const primeiroDia = new Date(
        Date.UTC(inicio.getUTCFullYear(), inicio.getUTCMonth(), inicio.getUTCDate()),
      );
      for (let cursor = primeiroDia.getTime(); cursor < fim.getTime(); cursor += DAY_MS) {
        const diaInicio = cursor;
        const diaFim = cursor + DAY_MS;
        const de = Math.max(inicio.getTime(), diaInicio);
        const ate = Math.min(fim.getTime(), diaFim);
        if (ate <= de) continue;
        const key = dayKey(new Date(diaInicio));
        const lista = mapa.get(key) ?? [];
        lista.push({
          key: `${slotKey}|${key}`,
          slotKey,
          name: slot.name,
          shifts: slot.shifts,
          from: Math.round((de - diaInicio) / MINUTE_MS),
          to: Math.round((ate - diaInicio) / MINUTE_MS),
          hasVet: slot.hasVet,
          running,
          closed,
          startsAt: primeiro.starts_at,
          endsAt: primeiro.ends_at,
          lane: 0,
          lanes: 1,
        });
        mapa.set(key, lista);
      }
    }

    // Sobreposição vira coluna: dois turnos no mesmo horário dividem a largura
    // do dia em vez de um cobrir o outro. É a única forma de ver que existem
    // dois, que é justamente o que a pilha de cartões escondia.
    for (const lista of mapa.values()) {
      lista.sort((a, b) => a.from - b.from || a.to - b.to);
      const fimDaFaixa: number[] = [];
      for (const seg of lista) {
        let faixa = fimDaFaixa.findIndex((fim) => fim <= seg.from);
        if (faixa === -1) {
          faixa = fimDaFaixa.length;
          fimDaFaixa.push(seg.to);
        } else {
          fimDaFaixa[faixa] = seg.to;
        }
        seg.lane = faixa;
      }
      const total = Math.max(1, fimDaFaixa.length);
      for (const seg of lista) seg.lanes = total;
    }
    return mapa;
  }, [slots, timezone]);

  /** Os turnos que ESTA clínica usa, com o horário mais frequente de cada um.
   *
   *  Agrupado por NOME, não por (nome, horário): a clínica tem "Diurno" e
   *  "Noturno", e cinco variações de minuto do mesmo Diurno são resultado da
   *  interface antiga, não turnos diferentes. O horário oferecido é o que mais
   *  se repete, e continua editável. */
  const templates = useMemo<ShiftTemplate[]>(() => {
    const porNome = new Map<string, Map<string, ShiftTemplate>>();
    for (const shift of shifts ?? []) {
      const inicio = clinicWall(shift.starts_at, timezone);
      const fim = clinicWall(shift.ends_at, timezone);
      const from = inicio.getUTCHours() * 60 + inicio.getUTCMinutes();
      const to = fim.getUTCHours() * 60 + fim.getUTCMinutes();
      const chave = `${from}|${to}`;
      const variantes = porNome.get(shift.name) ?? new Map<string, ShiftTemplate>();
      const atual = variantes.get(chave) ?? { name: shift.name, from, to, uses: 0 };
      atual.uses += 1;
      variantes.set(chave, atual);
      porNome.set(shift.name, variantes);
    }
    return [...porNome.values()]
      .map((variantes) =>
        [...variantes.values()].sort((a, b) => b.uses - a.uses)[0],
      )
      .sort((a, b) => b.uses - a.uses)
      .slice(0, 4);
  }, [shifts, timezone]);

  /** A faixa de horas desenhada. Vinte e quatro horas de grade com turnos só
   *  de tarde seriam metade da tela em branco; uma faixa fixa cortaria o turno
   *  da madrugada. A faixa vem dos dados, com uma hora de folga. */
  const [hourFrom, hourTo] = useMemo(() => {
    let menor = 24;
    let maior = 0;
    for (const dia of days) {
      for (const seg of byDay.get(dia.key) ?? []) {
        menor = Math.min(menor, Math.floor(seg.from / 60));
        maior = Math.max(maior, Math.ceil(seg.to / 60));
      }
    }
    if (menor > maior) return [6, 22];
    return [Math.max(0, menor - 1), Math.min(24, maior + 1)];
  }, [days, byDay]);

  const hours = useMemo(
    () => Array.from({ length: hourTo - hourFrom }, (_, index) => hourFrom + index),
    [hourFrom, hourTo],
  );

  const nowWall = clinicWall(new Date(), timezone);
  const hojeKey = dayKey(nowWall);
  const nowMinutes = nowWall.getUTCHours() * 60 + nowWall.getUTCMinutes();

  const onDuty = useMemo(() => {
    const now = Date.now();
    return (shifts ?? []).filter((shift) => {
      const start = Date.parse(shift.starts_at);
      const end = Date.parse(shift.ends_at);
      return shift.closed_at === null && start <= now && now < end;
    });
  }, [shifts]);

  const gaps = useMemo(() => {
    const vistos = new Set<string>();
    let total = 0;
    for (const dia of days) {
      for (const seg of byDay.get(dia.key) ?? []) {
        if (seg.hasVet || vistos.has(seg.slotKey)) continue;
        vistos.add(seg.slotKey);
        total += 1;
      }
    }
    return total;
  }, [days, byDay]);

  const startsIso = useMemo(() => clinicIso(startsAt, timezone), [startsAt, timezone]);
  const endsIso = useMemo(() => clinicIso(endsAt, timezone), [endsAt, timezone]);

  const pickedMember = shiftMember ? (memberById.get(shiftMember) ?? null) : null;
  // Só quem tem o papel de veterinário responde por um turno: o campo era um
  // checkbox livre e a tela chamava o resultado de "evidência de conformidade".
  const canBeResponsible = pickedMember?.role === "vet";
  const rosterReady =
    shiftName.trim() !== "" &&
    startsIso !== null &&
    endsIso !== null &&
    startsIso < endsIso &&
    shiftMember !== "";

  const detail = selected ? (slots.get(selected) ?? null) : null;

  /** Quem ainda NÃO está neste turno. Oferecer quem já está é oferecer uma
   *  linha duplicada, e é assim que a mesma pessoa aparece duas vezes. */
  const naoEscalados = useMemo(() => {
    const dentro = new Set((detail?.shifts ?? []).map((shift) => shift.membership_id));
    return activeMembers.filter((member) => !dentro.has(member.id));
  }, [detail, activeMembers]);

  const addingMember = addMember ? (memberById.get(addMember) ?? null) : null;
  /** Um veterinário entrando num turno que ainda não tem responsável é o caso
   *  em que a marcação quase sempre deveria estar ligada. Fica ligada, e à
   *  vista: default excelente não é decisão escondida. */
  const addAsVet = addingMember?.role === "vet" && !(detail?.hasVet ?? false);

  function describeMember(id: string): { name: string; license: string | null } {
    const member = memberById.get(id);
    if (!member) {
      return {
        name: t("handover.schedule.memberFallback", { id: id.slice(0, 8) }),
        license: null,
      };
    }
    return { name: member.name || id.slice(0, 8), license: license(member) };
  }

  /** Clicar num espaço vazio abre a criação de turno naquele dia.
   *
   *  Sem isto, quem via um buraco na quinta às 20h precisava traduzir aquilo
   *  em dois campos de data e hora à mão, do outro lado da tela. O sistema já
   *  sabe onde a pessoa apontou. */
  function startAt(dia: Date, minutos: number) {
    const arredondado = Math.round(minutos / 30) * 30;
    const inicio = new Date(dia.getTime() + arredondado * MINUTE_MS);
    setDraft({ day: dia });
    setSelected(null);
    setAdding(false);
    // A hora do clique só vira campo quando não há turno-modelo para oferecer:
    // com modelos, escolher "Noturno" é mais rápido e mais certo do que
    // aceitar o minuto onde o dedo caiu.
    if (templates.length === 0) {
      setManualTime(true);
      setStartsAt(localInput(inicio));
      setEndsAt(localInput(new Date(inicio.getTime() + 12 * 60 * MINUTE_MS)));
    } else {
      setManualTime(false);
      setStartsAt("");
      setEndsAt("");
      setShiftName("");
    }
  }

  /** Escolher um turno-modelo preenche nome e horários daquele dia.
   *
   *  O turno que atravessa a madrugada termina no dia SEGUINTE: um "Noturno
   *  19:00 às 07:00" com fim antes do início seria recusado pela API, e a
   *  pessoa não teria como saber por quê. */
  function pickTemplate(template: ShiftTemplate) {
    const dia = draft?.day;
    if (!dia) return;
    const inicio = new Date(dia.getTime() + template.from * MINUTE_MS);
    const fimBase = dia.getTime() + template.to * MINUTE_MS;
    const fim = new Date(template.to > template.from ? fimBase : fimBase + DAY_MS);
    setShiftName(template.name);
    setStartsAt(localInput(inicio));
    setEndsAt(localInput(fim));
  }

  /** Somar alguém a um turno que JÁ existe.
   *
   *  É a mesma escrita de sempre (uma linha por pessoa, com o mesmo nome e a
   *  mesma janela), só que a janela vem do turno em vez de ser redigitada. Era
   *  o redigitar que produzia turnos quase-iguais, separados por segundos. */
  async function addToShift(membershipId: string, asVet: boolean) {
    if (!detail) return;
    const base = detail.shifts[0];
    await run(async () => {
      await api.createShift({
        name: base.name,
        starts_at: base.starts_at,
        ends_at: base.ends_at,
        membership_id: membershipId,
        is_vet_responsible: asVet,
      });
      setAdding(false);
      setAddMember("");
      await loadShifts();
    });
  }

  async function createShift() {
    if (!rosterReady || !startsIso || !endsIso) return;
    await run(async () => {
      await api.createShift({
        name: shiftName.trim(),
        starts_at: startsIso,
        ends_at: endsIso,
        membership_id: shiftMember,
        is_vet_responsible: canBeResponsible && vetResponsible,
      });
      setShiftName("");
      setStartsAt("");
      setEndsAt("");
      setShiftMember("");
      setVetResponsible(false);
      setManualTime(false);
      setDraft(null);
      setNotice(null);
      await loadShifts();
    });
  }

  async function closeShift(shiftId: string) {
    await run(async () => {
      const result = await api.closeShift(shiftId);
      const missing = result.missing_review?.length ?? 0;
      setNotice(
        missing > 0
          ? t("handover.schedule.missingReview", { n: missing })
          : t("handover.schedule.noMissingReview"),
      );
      await loadShifts();
    });
  }

  function shiftWeek(delta: number) {
    setWeekStart(new Date(sunday.getTime() + delta * DAYS_IN_WEEK * DAY_MS));
    setSelected(null);
  }

  const rangeLabel = t("schedule.range", {
    from: clinic.day(new Date(sunday.getTime() - zoneOffsetMs(sunday, timezone))),
    to: clinic.day(
      new Date(
        sunday.getTime() +
          (DAYS_IN_WEEK - 1) * DAY_MS -
          zoneOffsetMs(new Date(sunday.getTime() + (DAYS_IN_WEEK - 1) * DAY_MS), timezone),
      ),
    ),
  });

  return (
    <>
      {error ? <ErrorState message={error} /> : null}

      <Section title={t("team.schedule.title")} hint={t("team.schedule.hint")}>
        {notice ? <AdminNote>{notice}</AdminNote> : null}
        {shiftsError ? (
          <ErrorState message={shiftsError} onRetry={() => void loadShifts()} />
        ) : null}

        <div className="hv-layout">
          <div>
            <div className="agenda-toolbar">
              <div className="agenda-nav">
                <button type="button" onClick={() => shiftWeek(-1)} aria-label={t("schedule.prev")}>
                  ‹
                </button>
                <button type="button" onClick={() => setWeekStart(new Date())}>
                  {t("schedule.today")}
                </button>
                <button type="button" onClick={() => shiftWeek(1)} aria-label={t("schedule.next")}>
                  ›
                </button>
              </div>
              <strong className="agenda-range">{rangeLabel}</strong>
            </div>

            {/* Esqueleto é "estou carregando". Depois de um erro, ele vira uma
                promessa que não vai se cumprir: a mensagem com "tentar de
                novo" já está logo acima, e a área continuava fingindo carregar
                para sempre. */}
            {shifts === null ? (
              shiftsError ? null : (
                <Skeleton rows={1} height={420} />
              )
            ) : (
              <div className="agenda-scroll">
                <div className="agenda" style={{ ["--hour-px" as string]: `${HOUR_PX}px` }}>
                <div className="agenda-corner" />
                {days.map((dia) => {
                  const hoje = dia.key === hojeKey;
                  return (
                    <div
                      key={`h-${dia.key}`}
                      className={hoje ? "agenda-col-head is-today" : "agenda-col-head"}
                    >
                      <span className="agenda-weekday">
                        {t(`schedule.weekday.${dia.wall.getUTCDay()}`)}
                      </span>
                      <span className="agenda-daynum tabular">{dia.wall.getUTCDate()}</span>
                    </div>
                  );
                })}

                <div className="agenda-hours">
                  {hours.map((hour) => (
                    <span key={hour} className="agenda-hour tabular">
                      {String(hour).padStart(2, "0")}:00
                    </span>
                  ))}
                </div>

                {days.map((dia) => {
                  const hoje = dia.key === hojeKey;
                  const segments = byDay.get(dia.key) ?? [];
                  return (
                    <div
                      key={dia.key}
                      className={hoje ? "agenda-col is-today" : "agenda-col"}
                      style={{ height: hours.length * HOUR_PX }}
                      onClick={(event) => {
                        // Só o fundo: clicar num bloco é selecionar o turno.
                        if (event.target !== event.currentTarget) return;
                        const box = event.currentTarget.getBoundingClientRect();
                        const minutos =
                          hourFrom * 60 + ((event.clientY - box.top) / HOUR_PX) * 60;
                        startAt(dia.wall, minutos);
                      }}
                    >
                      {hours.map((hour) => (
                        <div key={hour} className="agenda-line" style={{ height: HOUR_PX }} />
                      ))}

                      {/* Onde está o relógio da clínica agora. Numa escala, a
                          pergunta "quem está aí neste momento" é a primeira. */}
                      {hoje && nowMinutes >= hourFrom * 60 && nowMinutes <= hourTo * 60 ? (
                        <div
                          className="agenda-now"
                          style={{ top: ((nowMinutes - hourFrom * 60) / 60) * HOUR_PX }}
                        />
                      ) : null}

                      {segments.map((seg) => {
                        const top = ((seg.from - hourFrom * 60) / 60) * HOUR_PX;
                        const height = ((seg.to - seg.from) / 60) * HOUR_PX;
                        /* Sobreposição em cascata, não em fatias iguais. Cinco
                           turnos ao mesmo tempo num dia de 135px dariam blocos
                           de 27px, onde não cabe nem a hora. Aqui cada um
                           recua um degrau e vai até a direita: vê-se que são
                           cinco, e o de cima continua legível. */
                        const degrau = Math.min(16, 55 / Math.max(1, seg.lanes - 1));
                        const recuo = seg.lane * degrau;
                        const classes = [
                          "agenda-block",
                          seg.hasVet ? "" : "no-vet",
                          seg.running ? "is-now" : "",
                          seg.closed ? "is-closed" : "",
                          selected === seg.slotKey ? "is-selected" : "",
                        ]
                          .filter(Boolean)
                          .join(" ");
                        return (
                          <button
                            key={seg.key}
                            type="button"
                            className={classes}
                            style={{
                              top,
                              height: Math.max(height, 20),
                              left: `${recuo}%`,
                              width: `calc(${100 - recuo}% - 3px)`,
                              zIndex: 2 + seg.lane,
                            }}
                            onClick={() => setSelected(seg.slotKey)}
                          >
                            <span className="agenda-block-time tabular">
                              {clinic.time(seg.startsAt)}
                            </span>
                            <span className="agenda-block-name">{seg.name}</span>
                            <span className="agenda-block-people">
                              {seg.shifts.map((shift) => (
                                <span key={shift.id} className="agenda-avatar">
                                  {initials(describeMember(shift.membership_id).name)}
                                </span>
                              ))}
                            </span>
                            {!seg.hasVet ? (
                              <span className="agenda-block-warn">
                                {t("schedule.noVetShort")}
                              </span>
                            ) : null}
                          </button>
                        );
                      })}
                    </div>
                  );
                })}
                </div>
              </div>
            )}
          </div>

          <aside className="hv-aside">
            {/* O turno apontado na agenda: sua janela e sua EQUIPE.
                Somar alguém acontece aqui dentro, sem redigitar o turno. */}
            {detail ? (
              <Card>
                <div style={{ display: "grid", gap: 12 }}>
                  <div>
                    <span className="hv-eyebrow">{detail.name}</span>
                    <div className="tabular" style={{ fontSize: 13.5, color: "var(--ink-3)" }}>
                      {t("team.schedule.window", {
                        start: clinic.moment(detail.shifts[0].starts_at),
                        end: clinic.moment(detail.shifts[0].ends_at),
                      })}
                    </div>
                  </div>

                  {detail.shifts.map((shift) => {
                    const person = describeMember(shift.membership_id);
                    return (
                      <div key={shift.id} className="shift-person">
                        <span className="hv-avatar" aria-hidden="true">
                          {initials(person.name)}
                        </span>
                        <div style={{ minWidth: 0 }}>
                          <div style={{ fontSize: 13.5, fontWeight: 600 }}>{person.name}</div>
                          <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                            {person.license ?? t("handover.schedule.noLicense")}
                          </div>
                          {shift.is_vet_responsible ? (
                            <span
                              className="hv-seal"
                              style={{ color: "var(--ok)", background: "var(--ok-bg)" }}
                            >
                              {t("handover.schedule.vetBadge")}
                            </span>
                          ) : null}
                        </div>
                        {/* Encerrar turno é operação do plantão: capacidade
                            diferente de montar a escala, gate separado. */}
                        {shift.closed_at ? (
                          <span style={{ fontSize: 12, color: "var(--ink-3)" }}>
                            {t("handover.schedule.closed", {
                              time: clinic.moment(shift.closed_at),
                            })}
                          </span>
                        ) : (
                          <Gate can={CAN.shiftOperate}>
                            <Button
                              variant="secondary"
                              disabled={busy}
                              onClick={() => void closeShift(shift.id)}
                              style={{ padding: "7px 12px", fontSize: 13 }}
                            >
                              {t("handover.schedule.close")}
                            </Button>
                          </Gate>
                        )}
                      </div>
                    );
                  })}

                  {/* O pedido que faltava: mais gente no MESMO plantão. Sem
                      isto, a única forma era descrever o turno de novo, e um
                      minuto de diferença criava um turno paralelo. */}
                  <Gate can={CAN.shiftSchedule}>
                    {adding ? (
                      <div style={{ display: "grid", gap: 10 }}>
                        <Combobox
                          value={addMember}
                          onChange={setAddMember}
                          options={naoEscalados.map((member) => ({
                            value: member.id,
                            label: member.name || member.id.slice(0, 8),
                            hint: license(member) ?? t(`team.role.${member.role}`),
                            keywords: member.email,
                          }))}
                          placeholder={t("handover.schedule.membershipPick")}
                          autoFocus
                        />
                        {naoEscalados.length === 0 ? (
                          <p className="hv-muted">{t("schedule.everyoneIn")}</p>
                        ) : null}
                        {addAsVet ? (
                          <AdminNote tone="neutral">{t("schedule.willBeResponsible")}</AdminNote>
                        ) : null}
                        <div className="admin-toolbar">
                          <Button
                            disabled={!addMember || busy}
                            onClick={() => void addToShift(addMember, addAsVet)}
                          >
                            {t("schedule.addConfirm")}
                          </Button>
                          <Button
                            variant="secondary"
                            onClick={() => {
                              setAdding(false);
                              setAddMember("");
                            }}
                          >
                            {t("common.cancel")}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <button
                        type="button"
                        className="shift-add"
                        onClick={() => setAdding(true)}
                      >
                        {t("schedule.addPerson")}
                      </button>
                    )}
                  </Gate>

                  {!detail.hasVet ? (
                    <div className="hv-alert">
                      <span className="hv-alert-title">{t("handover.schedule.noVet")}</span>
                      <p className="hv-alert-text">{t("handover.schedule.noVetHint")}</p>
                    </div>
                  ) : null}

                  <button type="button" className="agenda-close" onClick={() => setSelected(null)}>
                    {t("schedule.closeDetail")}
                  </button>
                </div>
              </Card>
            ) : null}

            {/* O turno novo só existe depois que alguém aponta um dia. Um
                formulário de quatro campos parado o tempo todo é a tela
                perguntando antes de haver o que responder. */}
            {draft ? (
              <Gate can={CAN.shiftSchedule}>
                <Card>
                  <form
                    style={{ display: "grid", gap: 14 }}
                    onSubmit={(event) => {
                      event.preventDefault();
                      void createShift();
                    }}
                  >
                    <div>
                      <h3 style={{ fontSize: 16 }}>{t("schedule.newShift")}</h3>
                      {draft.day ? (
                        <p className="hv-muted" style={{ margin: "2px 0 0" }}>
                          {clinic.day(
                            new Date(
                              draft.day.getTime() - zoneOffsetMs(draft.day, timezone),
                            ),
                          )}
                        </p>
                      ) : null}
                    </div>

                    {/* Os turnos que esta clínica já usa. O nome e as horas do
                        plantão não mudam toda semana, então o sistema não
                        pergunta: ele oferece, e continua editável. */}
                    {templates.length > 0 && !manualTime ? (
                      <Field label={t("schedule.whichShift")}>
                        <div className="shift-templates">
                          {templates.map((template) => {
                            const escolhido =
                              shiftName === template.name && startsAt !== "";
                            return (
                              <button
                                key={`${template.name}|${template.from}`}
                                type="button"
                                aria-pressed={escolhido}
                                className={escolhido ? "shift-chip is-on" : "shift-chip"}
                                onClick={() => pickTemplate(template)}
                              >
                                <strong>{template.name}</strong>
                                <span className="tabular">
                                  {hhmm(template.from)} – {hhmm(template.to)}
                                </span>
                              </button>
                            );
                          })}
                          <button
                            type="button"
                            className="shift-other"
                            onClick={() => {
                              setManualTime(true);
                              if (draft.day && !startsAt) {
                                const inicio = new Date(draft.day.getTime() + 7 * 60 * MINUTE_MS);
                                setStartsAt(localInput(inicio));
                                setEndsAt(
                                  localInput(new Date(inicio.getTime() + 12 * 60 * MINUTE_MS)),
                                );
                              }
                            }}
                          >
                            {t("schedule.otherTime")}
                          </button>
                        </div>
                      </Field>
                    ) : null}

                    {manualTime || templates.length === 0 ? (
                      <>
                        <Field label={t("handover.schedule.name")}>
                          <input
                            style={inputStyle}
                            value={shiftName}
                            placeholder={t("handover.schedule.namePlaceholder")}
                            onChange={(event) => setShiftName(event.target.value)}
                          />
                        </Field>
                        <div className="form-grid-2">
                          <Field label={t("handover.schedule.startsAt")}>
                            <input
                              type="datetime-local"
                              style={inputStyle}
                              value={startsAt}
                              onChange={(event) => setStartsAt(event.target.value)}
                            />
                          </Field>
                          <Field label={t("handover.schedule.endsAt")}>
                            <input
                              type="datetime-local"
                              style={inputStyle}
                              value={endsAt}
                              onChange={(event) => setEndsAt(event.target.value)}
                            />
                          </Field>
                        </div>
                        <p className="hv-muted">
                          {t("team.schedule.timezoneHint", { zone: timezone })}
                        </p>
                      </>
                    ) : startsAt ? (
                      <p className="hv-muted tabular">
                        {t("team.schedule.window", {
                          start: clinic.moment(clinicIso(startsAt, timezone) ?? startsAt),
                          end: clinic.moment(clinicIso(endsAt, timezone) ?? endsAt),
                        })}
                      </p>
                    ) : null}

                    <Field label={t("handover.schedule.membership")}>
                      {/* E-mail entra só na busca (keywords): a lista já mostra
                          nome e registro, e homônimo se resolve pelo e-mail. */}
                      <Combobox
                        value={shiftMember}
                        onChange={(value) => {
                          setShiftMember(value);
                          // Trocar para alguém que não é veterinário não pode
                          // deixar a marca de responsável ligada por inércia.
                          if (memberById.get(value)?.role !== "vet") setVetResponsible(false);
                        }}
                        options={activeMembers.map((member) => ({
                          value: member.id,
                          label: member.name || member.id.slice(0, 8),
                          hint: license(member) ?? t(`team.role.${member.role}`),
                          keywords: member.email,
                        }))}
                        placeholder={t("handover.schedule.membershipPick")}
                      />
                      {/* Sem a lista de pessoas não há quem escalar, e o campo
                          vazio se leria como "esta clínica não tem ninguém". */}
                      {membersError ? <AdminNote tone="danger">{membersError}</AdminNote> : null}
                    </Field>

                    {canBeResponsible ? (
                      <>
                        <CheckRow
                          checked={vetResponsible}
                          onChange={setVetResponsible}
                          label={t("handover.schedule.vetResponsible")}
                          hint={t("handover.schedule.vetResponsibleHint")}
                        />
                        {/* Responsável sem registro no conselho não é evidência
                            de nada: quem fiscaliza pede nome E registro. */}
                        {vetResponsible &&
                        !license(
                          pickedMember ?? { license_number: null, license_authority: null },
                        ) ? (
                          <AdminNote tone="danger">{t("team.schedule.noLicenseWarning")}</AdminNote>
                        ) : null}
                      </>
                    ) : pickedMember ? (
                      <p className="hv-muted">{t("team.schedule.onlyVetResponsible")}</p>
                    ) : null}

                    <div className="admin-toolbar">
                      <Button type="submit" disabled={!rosterReady || busy}>
                        {t("handover.schedule.submit")}
                      </Button>
                      <Button
                        variant="secondary"
                        onClick={() => {
                          setDraft(null);
                          setManualTime(false);
                          setShiftName("");
                          setStartsAt("");
                          setEndsAt("");
                          setShiftMember("");
                          setVetResponsible(false);
                        }}
                      >
                        {t("common.cancel")}
                      </Button>
                    </div>
                  </form>
                </Card>
              </Gate>
            ) : null}

            {/* Sem nada apontado, a lateral responde a pergunta que sempre
                importa numa escala, e só ela. */}
            {!detail && !draft ? (
              <Card>
                <div style={{ display: "grid", gap: 10 }}>
                  <span className="hv-eyebrow">{t("handover.schedule.onDuty")}</span>
                  {onDuty.length === 0 ? (
                    <p className="hv-muted">{t("handover.schedule.onDutyEmpty")}</p>
                  ) : (
                    onDuty.map((shift) => {
                      const person = describeMember(shift.membership_id);
                      return (
                        <div key={shift.id} className="hv-person">
                          <span className="hv-avatar" aria-hidden="true">
                            {initials(person.name)}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 14, fontWeight: 600 }}>{person.name}</div>
                            <div style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                              {t("team.schedule.onDutyLine", {
                                shift: shift.name,
                                license: person.license ?? t("handover.schedule.noLicense"),
                              })}
                            </div>
                          </div>
                        </div>
                      );
                    })
                  )}
                  <p className="hv-muted">{t("schedule.clickHint")}</p>
                </div>
              </Card>
            ) : null}

            {gaps > 0 ? (
              <div className="hv-alert">
                <span className="hv-alert-title">
                  {t("handover.schedule.gapCount", { n: gaps })}
                </span>
                <p className="hv-alert-text">{t("handover.schedule.gapHint")}</p>
              </div>
            ) : null}

            <div className="hv-note-block">{t("handover.schedule.evidence")}</div>
          </aside>
        </div>
      </Section>

      {dialog}
    </>
  );
}
