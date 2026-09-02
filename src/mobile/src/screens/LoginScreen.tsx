import { useState } from "react";
import { useTranslation } from "react-i18next";
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { ErrorBanner, PrimaryButton, Screen } from "../components/ui";
import { theme } from "../theme";
import { useApiErrorMessage, useSession } from "../useSession";

export function LoginScreen() {
  const { t } = useTranslation();
  const { loginPersonal, loginStation } = useSession();
  const describeError = useApiErrorMessage();

  const [mode, setMode] = useState<"personal" | "station">("personal");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [clinicSlug, setClinicSlug] = useState("demo");
  const [stationKey, setStationKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (mode === "personal") await loginPersonal(email, password);
      else await loginStation(clinicSlug, stationKey);
    } catch (err) {
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
        <ScrollView contentContainerStyle={styles.content}>
          <Text style={styles.brand}>
            Plantão<Text style={{ color: theme.primary }}>Vet</Text>
          </Text>
          <Text style={styles.claim}>{t("login.title")}</Text>

          <View style={styles.tabs}>
            {(["personal", "station"] as const).map((option) => (
              <PrimaryButton
                key={option}
                label={t(`login.tab.${option}`)}
                tone={mode === option ? "primary" : "secondary"}
                onPress={() => setMode(option)}
              />
            ))}
          </View>

          <ErrorBanner message={error} />

          {mode === "personal" ? (
            <>
              <Field label={t("login.email")} value={email} onChange={setEmail} keyboard="email-address" />
              <Field label={t("login.password")} value={password} onChange={setPassword} secure />
            </>
          ) : (
            <>
              <Field label={t("login.clinicSlug")} value={clinicSlug} onChange={setClinicSlug} />
              <Field label={t("login.stationKey")} value={stationKey} onChange={setStationKey} secure />
              <Text style={styles.hint}>{t("login.stationHint")}</Text>
            </>
          )}

          <PrimaryButton
            label={busy ? t("common.loading") : t("login.submit")}
            onPress={submit}
            disabled={busy}
          />
        </ScrollView>
      </KeyboardAvoidingView>
    </Screen>
  );
}

function Field({
  label,
  value,
  onChange,
  secure,
  keyboard,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  secure?: boolean;
  keyboard?: "email-address";
}) {
  return (
    <View style={{ gap: 6 }}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        secureTextEntry={secure}
        keyboardType={keyboard}
        autoCapitalize="none"
        autoCorrect={false}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  // maxWidth centraliza o formulário no tablet em vez de esticar a linha.
  content: { padding: 24, gap: 16, paddingTop: 72, maxWidth: 520, width: "100%", alignSelf: "center" },
  brand: { fontSize: 26, fontWeight: "800", color: theme.ink },
  claim: { fontSize: 17, color: theme.ink2, lineHeight: 24 },
  tabs: { flexDirection: "row", gap: 8 },
  label: {
    fontSize: 12,
    fontWeight: "600",
    color: theme.ink2,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
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
  hint: { fontSize: 13, color: theme.ink3 },
});
