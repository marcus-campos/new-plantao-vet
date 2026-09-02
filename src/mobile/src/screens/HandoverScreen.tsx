import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { ApiError, api, asList } from "../api/client";
import type { HandoverReport } from "../api/types";
import { ErrorBanner, Loading, Pill, PrimaryButton, Screen } from "../components/ui";
import { stateColors, theme } from "../theme";
import { useApiErrorMessage } from "../useSession";

interface PatientRef {
  name: string;
  kennel: string | null;
}

interface Counters {
  done: number;
  partial: number;
  not_done: number;
  pending: number;
  overdue: number;
}

/** O esqueleto é JSON livre no contrato; leia com cuidado e nunca quebre por falta de chave. */
function readCounters(skeleton: Record<string, unknown> | null | undefined): Counters {
  const tasks = (skeleton?.tasks ?? {}) as Record<string, unknown>;
  const value = (key: string) => (typeof tasks[key] === "number" ? (tasks[key] as number) : 0);
  return {
    done: value("done"),
    partial: value("partial"),
    not_done: value("not_done"),
    pending: value("pending"),
    overdue: value("overdue"),
  };
}

/**
 * Receber plantão no celular, paciente a paciente.
 *
 * Regra dura da spec: boletim sem revisão do plantonista anterior aparece
 * INTEIRO, só com selo. Esconder o não revisado é esconder justamente o
 * plantão que correu mal.
 */
export function HandoverScreen({
  onBack,
  onNeedsOperator,
}: {
  onBack: () => void;
  onNeedsOperator: () => void;
}) {
  const { t, i18n } = useTranslation();
  const describeError = useApiErrorMessage();

  // Termômetro de "carimbo em série": conta do momento em que a tela abriu.
  const openedAt = useRef(Date.now());

  const [reports, setReports] = useState<HandoverReport[] | null>(null);
  const [patients, setPatients] = useState<Record<string, PatientRef>>({});
  const [acked, setAcked] = useState<Record<string, number>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      const [reportPage, board] = await Promise.all([api.handoverReports(), api.board()]);
      const map: Record<string, PatientRef> = {};
      for (const row of board.rows) {
        map[row.hospitalization_id] = { name: row.patient_name, kennel: row.kennel_name };
      }
      setPatients(map);
      setReports(asList(reportPage));
      setError(null);
    } catch (err) {
      setError(describeError(err));
      setReports((current) => current ?? []);
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const timeFmt = new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" });

  async function accept(report: HandoverReport) {
    setBusyId(report.id);
    setError(null);
    try {
      const seconds = Math.max(0, Math.round((Date.now() - openedAt.current) / 1000));
      await api.ackHandover(report.id, seconds);
      setAcked((current) => ({ ...current, [report.id]: Date.now() }));
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        onNeedsOperator();
        return;
      }
      setError(describeError(err));
    } finally {
      setBusyId(null);
    }
  }

  if (!reports) {
    return (
      <Screen>
        <ErrorBanner message={error} />
        <Loading />
      </Screen>
    );
  }

  const total = reports.length;
  const doneCount = reports.filter((report) => acked[report.id]).length;
  const progress = total === 0 ? 0 : doneCount / total;

  return (
    <Screen>
      <FlatList
        data={reports}
        keyExtractor={(report) => report.id}
        contentContainerStyle={styles.list}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={async () => {
              setRefreshing(true);
              await load();
              setRefreshing(false);
            }}
          />
        }
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>{t("handoverApp.title")}</Text>
            <Text style={styles.subtitle}>
              {t("handoverApp.subtitle", {
                total,
                time: timeFmt.format(new Date()),
              })}
            </Text>

            <View style={styles.progressCard}>
              <View style={styles.progressTrack}>
                <View style={[styles.progressFill, { width: `${Math.round(progress * 100)}%` }]} />
              </View>
              <Text style={styles.progressLabel}>
                {t("handoverApp.progress", { done: doneCount, total })}
              </Text>
            </View>

            <ErrorBanner message={error} />
          </View>
        }
        ListEmptyComponent={<Text style={styles.empty}>{t("handoverApp.empty")}</Text>}
        ListFooterComponent={
          <View style={styles.footer}>
            <Text style={styles.footerNote}>{t("handoverApp.footer")}</Text>
            <PrimaryButton label={t("common.back")} tone="secondary" onPress={onBack} />
          </View>
        }
        renderItem={({ item }) => {
          const counters = readCounters(item.skeleton);
          const patient = patients[item.hospitalization_id];
          const name = patient?.name ?? t("handoverApp.unknownPatient");
          const ackedAt = acked[item.id];
          const edge = ackedAt
            ? theme.okEdge
            : counters.overdue > 0
              ? theme.lateEdge
              : undefined;

          return (
            <View
              style={[
                styles.card,
                edge ? { borderLeftWidth: 4, borderLeftColor: edge } : null,
                ackedAt ? styles.cardAcked : null,
              ]}
            >
              <View style={styles.cardHead}>
                <View style={styles.nameRow}>
                  <Text style={styles.name}>{name}</Text>
                  {patient?.kennel ? <Text style={styles.kennel}>{patient.kennel}</Text> : null}
                </View>
                {ackedAt ? (
                  <Pill
                    label={t("handoverApp.received", { time: timeFmt.format(new Date(ackedAt)) })}
                    fg={theme.ok}
                    bg={theme.okBg}
                  />
                ) : item.reviewed_at === null ? (
                  <Pill label={t("handoverApp.notReviewed")} fg={theme.late} bg={theme.lateBg} />
                ) : null}
              </View>

              <View style={styles.chips}>
                <CounterChip label={t("handoverApp.count.done", { n: counters.done })} state="done" />
                {counters.not_done > 0 ? (
                  <CounterChip
                    label={t("handoverApp.count.not_done", { n: counters.not_done })}
                    state="not_done"
                  />
                ) : null}
                {counters.pending > 0 ? (
                  <CounterChip
                    label={t("handoverApp.count.pending", { n: counters.pending })}
                    state="due"
                  />
                ) : null}
                {counters.overdue > 0 ? (
                  <CounterChip
                    label={t("handoverApp.count.overdue", { n: counters.overdue })}
                    state="overdue"
                  />
                ) : null}
              </View>

              {item.reviewed_at === null ? (
                <Text style={styles.notReviewedHint}>{t("handoverApp.notReviewedHint")}</Text>
              ) : null}

              <Text style={[styles.narrative, ackedAt ? styles.narrativeAcked : null]}>
                {item.narrative && item.narrative.trim() !== ""
                  ? item.narrative
                  : t("handoverApp.noNarrative")}
              </Text>

              {ackedAt ? null : (
                <Pressable
                  accessibilityRole="button"
                  disabled={busyId === item.id}
                  onPress={() => void accept(item)}
                  style={({ pressed }) => [
                    styles.accept,
                    { opacity: busyId === item.id ? 0.5 : pressed ? 0.85 : 1 },
                  ]}
                >
                  <Text style={styles.acceptLabel}>{t("handoverApp.accept", { name })}</Text>
                </Pressable>
              )}
            </View>
          );
        }}
      />
    </Screen>
  );
}

function CounterChip({ label, state }: { label: string; state: string }) {
  const colors = stateColors(state);
  return <Pill label={label} fg={colors.fg} bg={colors.bg} />;
}

const styles = StyleSheet.create({
  list: {
    padding: 20,
    paddingTop: 60,
    gap: 10,
    maxWidth: 640,
    width: "100%",
    alignSelf: "center",
  },
  header: { gap: 4, paddingBottom: 12 },
  title: { fontSize: 24, fontWeight: "800", color: theme.ink },
  subtitle: { fontSize: 14, color: theme.ink3 },
  progressCard: {
    marginTop: 12,
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  progressTrack: {
    flex: 1,
    height: 8,
    borderRadius: 999,
    backgroundColor: theme.tint,
    overflow: "hidden",
  },
  progressFill: { height: "100%", backgroundColor: theme.primary, borderRadius: 999 },
  progressLabel: { fontSize: 13, fontWeight: "600", color: theme.ink2 },
  card: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 12,
    padding: 16,
    gap: 10,
  },
  cardAcked: { opacity: 0.75 },
  cardHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 8, flex: 1, flexWrap: "wrap" },
  name: { fontSize: 16, fontWeight: "700", color: theme.ink },
  kennel: { fontSize: 13, color: theme.ink3 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  notReviewedHint: { fontSize: 13, fontWeight: "600", color: theme.late, lineHeight: 19 },
  narrative: { fontSize: 14, lineHeight: 21, color: theme.ink },
  narrativeAcked: { fontSize: 13.5, color: theme.ink2 },
  accept: {
    minHeight: 48,
    borderRadius: 10,
    backgroundColor: theme.primary,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16,
  },
  acceptLabel: { color: "#fff", fontWeight: "700", fontSize: 15 },
  empty: { fontSize: 14, color: theme.ink3, paddingVertical: 24, textAlign: "center" },
  footer: { paddingTop: 14, gap: 14 },
  footerNote: { textAlign: "center", fontSize: 12.5, color: theme.ink3 },
});
