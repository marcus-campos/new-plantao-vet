import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { ApiError, api } from "../api/client";
import type { OutcomeReason, Task } from "../api/types";
import { Card, ErrorBanner, Pill, PrimaryButton, Screen } from "../components/ui";
import { stateColors, theme } from "../theme";
import { useApiErrorMessage } from "../useSession";

const REASONS: OutcomeReason[] = ["refused", "fasting", "unavailable", "vet_order", "other"];

/** Baixa de tarefa no ponto de cuidado: confirmar, ou não realizar com motivo. */
export function TaskScreen({
  task,
  onDone,
  onBack,
  onNeedsOperator,
}: {
  task: Task;
  onDone: () => void;
  onBack: () => void;
  onNeedsOperator: () => void;
}) {
  const { t, i18n } = useTranslation();
  const describeError = useApiErrorMessage();
  const [mode, setMode] = useState<"execute" | "not_done">("execute");
  const [reason, setReason] = useState<OutcomeReason>("fasting");
  const [detail, setDetail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const colors = stateColors(task.display_state);
  const timeFmt = new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" });

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        onNeedsOperator();
        return;
      }
      if (err instanceof ApiError && err.code === "early_confirmation_required") {
        // A janela vale nos dois lados: adiantar dose é erro como atrasar.
        setError(
          t("task.confirmEarly", { time: timeFmt.format(new Date(task.scheduled_for)) }),
        );
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <ScrollView contentContainerStyle={styles.content}>
        <Card edge={colors.edge}>
          <View style={{ gap: 8 }}>
            <View style={styles.headerRow}>
              <Text style={styles.title}>{task.title}</Text>
              <Pill label={t(`state.${task.display_state}`)} fg={colors.fg} bg={colors.bg} />
            </View>
            <Text style={styles.meta}>
              {timeFmt.format(new Date(task.scheduled_for))}
              {task.criticality === "critical" ? ` · ${t("sheet.criticality.critical")}` : ""}
            </Text>
          </View>
        </Card>

        <ErrorBanner message={error} />

        {mode === "execute" ? (
          <View style={{ gap: 10 }}>
            <PrimaryButton
              label={t("task.execute")}
              disabled={busy}
              onPress={() => void run(() => api.executeTask(task.id))}
            />
            <PrimaryButton
              label={t("task.notDone")}
              tone="secondary"
              onPress={() => setMode("not_done")}
            />
            {error?.includes("?") ? (
              <PrimaryButton
                label={t("pin.confirm")}
                tone="danger"
                disabled={busy}
                onPress={() => void run(() => api.executeTask(task.id, { confirm_early: true }))}
              />
            ) : null}
          </View>
        ) : (
          <View style={{ gap: 10 }}>
            {REASONS.map((option) => (
              <PrimaryButton
                key={option}
                label={t(`task.reason.${option}`)}
                tone={reason === option ? "primary" : "secondary"}
                onPress={() => setReason(option)}
              />
            ))}
            {reason === "other" ? (
              <TextInput
                style={styles.input}
                placeholder={t("task.reasonDetail")}
                value={detail}
                onChangeText={setDetail}
              />
            ) : null}
            <PrimaryButton
              label={t("task.notDone")}
              tone="danger"
              disabled={busy || (reason === "other" && detail.trim() === "")}
              onPress={() =>
                void run(() =>
                  api.notDoneTask(
                    task.id,
                    reason,
                    reason === "other" ? { outcome_detail: detail } : undefined,
                  ),
                )
              }
            />
            <PrimaryButton
              label={t("common.cancel")}
              tone="secondary"
              onPress={() => setMode("execute")}
            />
          </View>
        )}

        <PrimaryButton label={t("common.back")} tone="secondary" onPress={onBack} />
      </ScrollView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    padding: 20,
    paddingTop: 64,
    gap: 16,
    maxWidth: 520,
    width: "100%",
    alignSelf: "center",
  },
  headerRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 10 },
  title: { fontSize: 19, fontWeight: "700", color: theme.ink, flex: 1 },
  meta: { fontSize: 14, color: theme.ink2 },
  input: {
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 10,
    paddingHorizontal: 14,
    minHeight: 48,
    backgroundColor: theme.surface,
    fontSize: 16,
    color: theme.ink,
  },
});
