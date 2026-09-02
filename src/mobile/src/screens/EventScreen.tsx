import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from "react-native";

import { ApiError, api } from "../api/client";
import { ErrorBanner, PrimaryButton, Screen } from "../components/ui";
import { TOUCH_TARGET, theme } from "../theme";
import { useApiErrorMessage } from "../useSession";

/** Os eventos que o plantão vê fora da grade. `other` abre título livre. */
const TYPES = ["vomit", "diarrhea", "seizure", "urination", "defecation", "other"] as const;
type EventType = (typeof TYPES)[number];

/** Registrar evento fora da grade: vira tarefa ad-hoc já executada, com hora e autor. */
export function EventScreen({
  hospitalizationId,
  patientName,
  onDone,
  onCancel,
  onNeedsOperator,
}: {
  hospitalizationId: string;
  patientName: string;
  onDone: () => void;
  onCancel: () => void;
  onNeedsOperator: () => void;
}) {
  const { t, i18n } = useTranslation();
  const describeError = useApiErrorMessage();

  const [type, setType] = useState<EventType>("vomit");
  const [otherTitle, setOtherTitle] = useState("");
  const [note, setNote] = useState("");
  const [notifyVet, setNotifyVet] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => new Date());

  // O botão mostra a hora que será registrada; não deixamos envelhecer na tela.
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(timer);
  }, []);

  const timeFmt = new Intl.DateTimeFormat(i18n.language, { hour: "2-digit", minute: "2-digit" });
  const title = type === "other" ? otherTitle.trim() : t(`event.type.${type}`);
  const incomplete = title === "";

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.adHocTask({
        hospitalization_id: hospitalizationId,
        title,
        category: "care",
        values: { note: note.trim(), notify_vet: notifyVet },
      });
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        onNeedsOperator();
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={{ gap: 2 }}>
            <Text style={styles.title}>{t("event.title")}</Text>
            <Text style={styles.subtitle}>{patientName}</Text>
          </View>

          <ErrorBanner message={error} />

          <View style={{ gap: 8 }}>
            <Text style={styles.label}>{t("event.typeLabel")}</Text>
            <View style={styles.chips}>
              {TYPES.map((option) => {
                const selected = option === type;
                return (
                  <Pressable
                    key={option}
                    onPress={() => setType(option)}
                    accessibilityRole="button"
                    accessibilityState={{ selected }}
                    style={({ pressed }) => [
                      styles.chip,
                      selected ? styles.chipOn : null,
                      { opacity: pressed ? 0.85 : 1 },
                    ]}
                  >
                    <Text style={[styles.chipLabel, selected ? styles.chipLabelOn : null]}>
                      {t(`event.type.${option}`)}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          {type === "other" ? (
            <View style={{ gap: 6 }}>
              <Text style={styles.label}>{t("event.otherLabel")}</Text>
              <TextInput
                style={styles.input}
                value={otherTitle}
                onChangeText={setOtherTitle}
                placeholder={t("event.otherPlaceholder")}
                placeholderTextColor={theme.inkMuted}
                autoFocus
              />
            </View>
          ) : null}

          <View style={{ gap: 6 }}>
            <Text style={styles.label}>{t("event.noteLabel")}</Text>
            <TextInput
              style={styles.textarea}
              value={note}
              onChangeText={setNote}
              placeholder={t("event.notePlaceholder")}
              placeholderTextColor={theme.inkMuted}
              multiline
              textAlignVertical="top"
            />
          </View>

          <Pressable
            onPress={() => setNotifyVet((value) => !value)}
            accessibilityRole="switch"
            accessibilityState={{ checked: notifyVet }}
            accessibilityLabel={t("event.notifyVet")}
            style={styles.toggleRow}
          >
            <View style={{ flex: 1, gap: 1 }}>
              <Text style={styles.toggleTitle}>{t("event.notifyVet")}</Text>
              <Text style={styles.toggleHint}>{t("event.notifyVetHint")}</Text>
            </View>
            <Switch
              value={notifyVet}
              onValueChange={setNotifyVet}
              trackColor={{ false: theme.line, true: theme.primary }}
              thumbColor={theme.surface}
            />
          </Pressable>

          <View style={{ gap: 10 }}>
            <PrimaryButton
              label={`${busy ? t("common.loading") : t("event.submit")} · ${timeFmt.format(now)}`}
              disabled={busy || incomplete}
              onPress={() => void submit()}
            />
            <PrimaryButton label={t("common.cancel")} tone="secondary" onPress={onCancel} />
            <Text style={styles.footer}>{t("event.hint")}</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  // 54px de espaçador como nas demais telas; maxWidth centraliza no tablet.
  content: {
    padding: 20,
    paddingTop: 54,
    gap: 16,
    maxWidth: 520,
    width: "100%",
    alignSelf: "center",
  },
  title: { fontSize: 20, fontWeight: "800", color: theme.ink },
  subtitle: { fontSize: 13, color: theme.ink3 },
  label: {
    fontSize: 12,
    fontWeight: "700",
    color: theme.ink2,
    textTransform: "uppercase",
    letterSpacing: 0.6,
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: {
    minHeight: TOUCH_TARGET,
    justifyContent: "center",
    borderRadius: 999,
    borderWidth: 1,
    borderColor: theme.line,
    backgroundColor: theme.surface,
    paddingHorizontal: 18,
  },
  chipOn: { backgroundColor: theme.primary, borderColor: theme.primary },
  chipLabel: { fontSize: 14, fontWeight: "600", color: theme.ink2 },
  chipLabelOn: { color: "#fff", fontWeight: "700" },
  input: {
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 10,
    paddingHorizontal: 14,
    minHeight: TOUCH_TARGET,
    backgroundColor: theme.surface,
    fontSize: 16,
    color: theme.ink,
  },
  textarea: {
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 10,
    padding: 14,
    minHeight: 108,
    backgroundColor: theme.surface,
    fontSize: 16,
    lineHeight: 23,
    color: theme.ink,
  },
  toggleRow: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 12,
    padding: 14,
    minHeight: TOUCH_TARGET,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  toggleTitle: { fontSize: 15, fontWeight: "600", color: theme.ink },
  toggleHint: { fontSize: 13, color: theme.ink3 },
  footer: { fontSize: 12.5, color: theme.ink3, textAlign: "center" },
});
