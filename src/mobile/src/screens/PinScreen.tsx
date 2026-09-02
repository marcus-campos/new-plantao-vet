import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { ErrorBanner, PrimaryButton, Screen } from "../components/ui";
import { theme } from "../theme";
import { useApiErrorMessage, useSession } from "../useSession";

const KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"];

/** Modo estação num único celular da clínica: identifica quem executa. */
export function PinScreen({
  context,
  onDone,
  onCancel,
}: {
  context?: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const { identifyOperator } = useSession();
  const describeError = useApiErrorMessage();
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(value: string) {
    setBusy(true);
    setError(null);
    try {
      await identifyOperator(value);
      onDone();
    } catch (err) {
      setError(describeError(err));
      setPin("");
    } finally {
      setBusy(false);
    }
  }

  function press(key: string) {
    if (busy) return;
    if (key === "⌫") {
      setPin((current) => current.slice(0, -1));
      return;
    }
    if (!key || pin.length >= 4) return;
    const next = pin + key;
    setPin(next);
    if (next.length === 4) void submit(next);
  }

  return (
    <Screen>
      <View style={styles.content}>
        <Text style={styles.title}>{t("pin.title")}</Text>
        {context ? <Text style={styles.context}>{context}</Text> : null}

        <View style={styles.dots}>
          {[0, 1, 2, 3].map((index) => (
            <View
              key={index}
              style={[styles.dot, index < pin.length ? styles.dotFilled : null]}
            />
          ))}
        </View>

        <ErrorBanner message={error} />

        <View style={styles.keypad}>
          {KEYS.map((key, index) => (
            <Pressable
              key={index}
              onPress={() => press(key)}
              disabled={!key || busy}
              accessibilityRole="button"
              accessibilityLabel={key || undefined}
              style={({ pressed }) => [
                styles.key,
                !key ? styles.keyEmpty : null,
                pressed && key ? { backgroundColor: theme.tint } : null,
              ]}
            >
              <Text style={styles.keyLabel}>{key}</Text>
            </Pressable>
          ))}
        </View>

        <Text style={styles.hint}>{t("pin.hint")}</Text>
        <PrimaryButton label={t("common.cancel")} tone="secondary" onPress={onCancel} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  content: {
    flex: 1,
    padding: 24,
    paddingTop: 72,
    gap: 16,
    maxWidth: 480,
    width: "100%",
    alignSelf: "center",
  },
  title: { fontSize: 22, fontWeight: "800", color: theme.ink },
  context: { fontSize: 14, color: theme.ink3 },
  dots: { flexDirection: "row", gap: 14, justifyContent: "center", paddingVertical: 12 },
  dot: { width: 16, height: 16, borderRadius: 8, backgroundColor: theme.line },
  dotFilled: { backgroundColor: theme.primary },
  keypad: { flexDirection: "row", flexWrap: "wrap", gap: 10, justifyContent: "center" },
  key: {
    width: "30%",
    minHeight: 72,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: theme.line,
    backgroundColor: theme.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  keyEmpty: { borderColor: "transparent", backgroundColor: "transparent" },
  keyLabel: { fontSize: 24, fontWeight: "600", color: theme.ink },
  hint: { fontSize: 13, color: theme.ink3, textAlign: "center" },
});
