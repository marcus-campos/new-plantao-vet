import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { api, asList } from "../api/client";
import type { HospitalizationDetail, ShiftNote, Task } from "../api/types";
import { ErrorBanner, Loading, Pill, PrimaryButton, Screen } from "../components/ui";
import { stateColors, theme } from "../theme";
import { useApiErrorMessage } from "../useSession";

const DAY_MS = 24 * 60 * 60 * 1000;
const MAX_NOTES = 5;

/** `details` é JSON livre no contrato: leia sem confiar no tipo. */
function readNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
    return Number(value);
  }
  return null;
}

/** Ficha resumida do paciente: o que o plantonista precisa ver de pé, ao lado do box. */
export function PatientScreen({
  hospitalizationId,
  onBack,
  onOpenTask,
  onAddNote,
  onAddEvent,
}: {
  hospitalizationId: string;
  onBack: () => void;
  onOpenTask: (task: Task) => void;
  onAddNote: () => void;
  onAddEvent: () => void;
}) {
  const { t, i18n } = useTranslation();
  const describeError = useApiErrorMessage();

  const [detail, setDetail] = useState<HospitalizationDetail | null>(null);
  const [notes, setNotes] = useState<ShiftNote[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [loaded, noteList] = await Promise.all([
        api.hospitalization(hospitalizationId),
        api.shiftNotes(hospitalizationId),
      ]);
      setDetail(loaded);
      setNotes(asList(noteList));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [describeError, hospitalizationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const timeFmt = new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" });
  const numberFmt = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });

  if (!detail) {
    return (
      <Screen>
        <ErrorBanner message={error} />
        <Loading />
      </Screen>
    );
  }

  const patient = detail.patient;
  const active = detail.prescriptions.filter((item) => item.suspended_at === null);
  const continuous = active.filter((item) => item.kind === "continuous");
  const prn = active.filter((item) => item.kind === "prn");
  const hasCritical = active.some((item) => item.criticality === "critical");

  const admitted = new Date(detail.hospitalization.admitted_at);
  const day = Math.max(1, Math.floor((Date.now() - admitted.getTime()) / DAY_MS) + 1);

  const weight = patient?.weight_kg ? readNumber(patient.weight_kg) : null;
  const metaParts = [
    patient?.species,
    patient?.breed,
    weight === null ? null : t("patientApp.weight", { value: numberFmt.format(weight) }),
    detail.kennel_name,
    t("patientApp.day", { days: day }),
  ].filter((part): part is string => Boolean(part));

  const pending = detail.tasks
    .filter((task) => task.status === "pending")
    .sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for));

  const lastNotes = [...notes]
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, MAX_NOTES);

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("common.back")}
            onPress={onBack}
            style={({ pressed }) => [styles.back, { opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={styles.backGlyph}>←</Text>
          </Pressable>
          <View style={styles.headerText}>
            <View style={styles.titleRow}>
              <Text style={styles.title}>{patient?.name ?? t("patientApp.unknownPatient")}</Text>
              {hasCritical ? (
                <Pill label={t("patientApp.critical")} fg={theme.late} bg={theme.lateBg} />
              ) : null}
            </View>
            <Text style={styles.meta}>{metaParts.join(" · ")}</Text>
            {detail.vet_name ? (
              <Text style={styles.meta}>
                {t("sheet.vet")}: {detail.vet_name}
                {detail.vet_license ? ` · ${detail.vet_license}` : ""}
              </Text>
            ) : null}
          </View>
        </View>

        <ErrorBanner message={error} />

        <View style={styles.card}>
          <Text style={styles.sectionLabel}>{t("patientApp.section.ongoing")}</Text>
          {continuous.length === 0 && prn.length === 0 ? (
            <Text style={styles.emptyLine}>{t("patientApp.empty.ongoing")}</Text>
          ) : null}
          {continuous.map((item) => {
            const rate = readNumber(item.details?.rate_ml_h);
            return (
              <OngoingRow
                key={item.id}
                dot={theme.primary}
                name={item.name}
                detail={
                  rate === null
                    ? ""
                    : t("patientApp.rate", { value: numberFmt.format(rate) })
                }
                strong
              />
            );
          })}
          {prn.map((item) => (
            <OngoingRow
              key={item.id}
              dot={theme.warnEdge}
              name={`${item.name} · ${t("sheet.prn")}`}
              detail={[
                item.max_doses_24h === null
                  ? null
                  : t("patientApp.prnMax", { max: item.max_doses_24h }),
                item.min_interval_minutes === null
                  ? null
                  : t("patientApp.prnInterval", { minutes: item.min_interval_minutes }),
              ]
                .filter((part): part is string => Boolean(part))
                .join(" · ")}
            />
          ))}
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionLabel}>{t("patientApp.section.nextTasks")}</Text>
          {pending.length === 0 ? (
            <Text style={styles.emptyLine}>{t("patientApp.empty.tasks")}</Text>
          ) : null}
          {pending.map((task) => {
            const colors = stateColors(task.display_state);
            return (
              <Pressable
                key={task.id}
                accessibilityRole="button"
                onPress={() => onOpenTask(task)}
                style={({ pressed }) => [styles.taskRow, { opacity: pressed ? 0.85 : 1 }]}
              >
                <Text style={styles.taskTime}>
                  {timeFmt.format(new Date(task.scheduled_for))}
                </Text>
                <View style={styles.taskBody}>
                  <Text style={styles.taskTitle}>{task.title}</Text>
                  {task.criticality === "critical" ? (
                    <Text style={styles.taskCritical}>{t("sheet.criticality.critical")}</Text>
                  ) : null}
                </View>
                <Pill label={t(`state.${task.display_state}`)} fg={colors.fg} bg={colors.bg} />
              </Pressable>
            );
          })}
        </View>

        <View style={styles.card}>
          <Text style={styles.sectionLabel}>{t("patientApp.section.notes")}</Text>
          {lastNotes.length === 0 ? (
            <Text style={styles.emptyLine}>{t("patientApp.empty.notes")}</Text>
          ) : null}
          {lastNotes.map((note) => (
            <View key={note.id} style={styles.note}>
              <Text style={styles.noteText}>{note.text}</Text>
              <Text style={styles.noteMeta}>
                {note.author_name} · {timeFmt.format(new Date(note.created_at))}
                {note.source === "audio" ? ` · 🎙 ${t("patientApp.audioNote")}` : ""}
              </Text>
            </View>
          ))}
        </View>

        <View style={styles.actions}>
          <View style={styles.actionMain}>
            <PrimaryButton label={t("patientApp.addEvent")} onPress={onAddEvent} />
          </View>
          <View style={styles.actionSecondary}>
            <PrimaryButton label={t("patientApp.addNote")} tone="secondary" onPress={onAddNote} />
          </View>
        </View>
      </ScrollView>
    </Screen>
  );
}

function OngoingRow({
  dot,
  name,
  detail,
  strong,
}: {
  dot: string;
  name: string;
  detail: string;
  strong?: boolean;
}) {
  return (
    <View style={styles.ongoingRow}>
      <View style={[styles.dot, { backgroundColor: dot }]} />
      <Text style={styles.ongoingName}>{name}</Text>
      {detail ? (
        <Text style={strong ? styles.ongoingValueStrong : styles.ongoingValue}>{detail}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 20,
    paddingTop: 60,
    gap: 12,
    maxWidth: 640,
    width: "100%",
    alignSelf: "center",
  },
  header: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  back: {
    width: 48,
    height: 48,
    marginLeft: -12,
    alignItems: "center",
    justifyContent: "center",
  },
  backGlyph: { fontSize: 24, color: theme.ink, fontWeight: "600" },
  headerText: { flex: 1, gap: 3, paddingTop: 6 },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 8, flexWrap: "wrap" },
  title: { fontSize: 24, fontWeight: "800", color: theme.ink },
  meta: { fontSize: 13, color: theme.ink3 },
  card: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 12,
    padding: 16,
    gap: 10,
  },
  sectionLabel: {
    fontSize: 11,
    letterSpacing: 1,
    textTransform: "uppercase",
    color: theme.ink3,
    fontWeight: "600",
  },
  emptyLine: { fontSize: 13.5, color: theme.ink3 },
  ongoingRow: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 28 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  ongoingName: { fontSize: 14, color: theme.ink, flex: 1 },
  ongoingValue: { fontSize: 13, color: theme.ink3 },
  ongoingValueStrong: { fontSize: 14, fontWeight: "600", color: theme.ink },
  taskRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 48,
  },
  taskTime: { fontWeight: "700", fontSize: 14, color: theme.ink, width: 52 },
  taskBody: { flex: 1, gap: 2 },
  taskTitle: { fontSize: 14, color: theme.ink },
  taskCritical: { fontSize: 12, fontWeight: "700", color: theme.late },
  note: { gap: 3 },
  noteText: { fontSize: 13.5, lineHeight: 20, color: theme.ink2, fontStyle: "italic" },
  noteMeta: { fontSize: 12, color: theme.ink3 },
  actions: { flexDirection: "row", gap: 10, paddingTop: 2, paddingBottom: 12 },
  actionMain: { flex: 2 },
  actionSecondary: { flex: 1 },
});
