import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View, useWindowDimensions } from "react-native";

import { api } from "../api/client";
import type { Task } from "../api/types";
import { ErrorBanner, Loading, Pill, PrimaryButton, Screen } from "../components/ui";
import { stateColors, theme } from "../theme";
import { useApiErrorMessage } from "../useSession";

/** Meu turno: a fila da janela, ordenada por horário. Atrasado vem primeiro na leitura. */
export function ShiftScreen({
  onOpenTask,
  onOpenHandover,
  onOpenPatient,
}: {
  onOpenTask: (task: Task) => void;
  onOpenHandover: () => void;
  onOpenPatient: (hospitalizationId: string, patientName: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const describeError = useApiErrorMessage();
  const { width } = useWindowDimensions();
  const twoColumns = width >= 700; // tablet: duas colunas em vez de esticar a linha

  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setTasks(await api.allTasks());
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  const timeFmt = new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" });
  const overdue = tasks?.filter((task) => task.display_state === "overdue") ?? [];

  if (!tasks) {
    return (
      <Screen>
        <ErrorBanner message={error} />
        <Loading />
      </Screen>
    );
  }

  return (
    <Screen>
      <View style={styles.header}>
        <Text style={styles.title}>{t("nav.board")}</Text>
        <Text style={styles.subtitle}>
          {overdue.length > 0
            ? `${overdue.length} ${t("board.overdue")}`
            : t("state.on_time")}
        </Text>
      </View>

      <ErrorBanner message={error} />

      <View style={styles.actions}>
        <PrimaryButton label={t("nav.handover")} tone="secondary" onPress={onOpenHandover} />
      </View>

      <FlatList
        data={tasks}
        keyExtractor={(task) => task.id}
        numColumns={twoColumns ? 2 : 1}
        key={twoColumns ? "two" : "one"}
        columnWrapperStyle={twoColumns ? { gap: 10 } : undefined}
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
        renderItem={({ item }) => {
          const colors = stateColors(item.display_state);
          return (
            <Pressable
              onPress={() => onOpenTask(item)}
              onLongPress={() => onOpenPatient(item.hospitalization_id, item.title)}
              accessibilityRole="button"
              accessibilityHint={t("shift.longPressHint")}
              style={({ pressed }) => [
                styles.row,
                { borderLeftColor: colors.edge, opacity: pressed ? 0.85 : 1, flex: twoColumns ? 1 : undefined },
              ]}
            >
              <Text style={styles.time}>{timeFmt.format(new Date(item.scheduled_for))}</Text>
              <View style={{ flex: 1, gap: 2 }}>
                <Text style={styles.taskTitle}>{item.title}</Text>
                {item.criticality === "critical" ? (
                  <Text style={styles.critical}>{t("sheet.criticality.critical")}</Text>
                ) : null}
              </View>
              <Pill label={t(`state.${item.display_state}`)} fg={colors.fg} bg={colors.bg} />
            </Pressable>
          );
        }}
      />
    </Screen>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: 20, paddingTop: 60, paddingBottom: 12, gap: 2 },
  actions: { paddingHorizontal: 20, paddingBottom: 10 },
  title: { fontSize: 24, fontWeight: "800", color: theme.ink },
  subtitle: { fontSize: 14, color: theme.ink3 },
  list: { padding: 20, paddingTop: 4, gap: 10 },
  row: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderLeftWidth: 4,
    borderRadius: 12,
    padding: 14,
    minHeight: 64,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  time: { fontWeight: "700", fontSize: 15, color: theme.ink, width: 52 },
  taskTitle: { fontSize: 15, fontWeight: "600", color: theme.ink },
  critical: { fontSize: 12, fontWeight: "700", color: theme.late },
});
