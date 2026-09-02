import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import { TOUCH_TARGET, theme } from "../theme";

export function Screen({ children }: { children: ReactNode }) {
  return <View style={styles.screen}>{children}</View>;
}

export function Card({ children, edge }: { children: ReactNode; edge?: string }) {
  return (
    <View style={[styles.card, edge ? { borderLeftWidth: 4, borderLeftColor: edge } : null]}>
      {children}
    </View>
  );
}

export function PrimaryButton({
  label,
  onPress,
  disabled,
  tone = "primary",
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  tone?: "primary" | "secondary" | "danger";
}) {
  const palette =
    tone === "primary"
      ? { background: theme.primary, color: "#fff", border: theme.primary }
      : tone === "danger"
        ? { background: theme.lateBg, color: theme.late, border: theme.lateEdge }
        : { background: theme.surface, color: theme.ink2, border: theme.line };
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: palette.background,
          borderColor: palette.border,
          opacity: disabled ? 0.5 : pressed ? 0.85 : 1,
        },
      ]}
    >
      <Text style={[styles.buttonLabel, { color: palette.color }]}>{label}</Text>
    </Pressable>
  );
}

export function Pill({ label, fg, bg }: { label: string; fg: string; bg: string }) {
  return (
    <View style={[styles.pill, { backgroundColor: bg }]}>
      <Text style={[styles.pillLabel, { color: fg }]}>{label}</Text>
    </View>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <View style={styles.error} accessibilityRole="alert">
      <Text style={styles.errorText}>{message}</Text>
    </View>
  );
}

export function Loading() {
  return (
    <View style={styles.loading}>
      <ActivityIndicator color={theme.primary} />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: theme.ground },
  card: {
    backgroundColor: theme.surface,
    borderWidth: 1,
    borderColor: theme.line,
    borderRadius: 12,
    padding: 16,
  },
  button: {
    minHeight: TOUCH_TARGET,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  buttonLabel: { fontSize: 16, fontWeight: "700" },
  pill: { borderRadius: 999, paddingHorizontal: 12, paddingVertical: 5 },
  pillLabel: { fontSize: 12, fontWeight: "700" },
  error: {
    backgroundColor: theme.lateBg,
    borderWidth: 1,
    borderColor: theme.lateEdge,
    borderRadius: 10,
    padding: 12,
  },
  errorText: { color: theme.late, fontSize: 14, fontWeight: "500" },
  loading: { padding: 32, alignItems: "center" },
});
