import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Animated,
  Easing,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import {
  RecordingPresets,
  getRecordingPermissionsAsync,
  requestRecordingPermissionsAsync,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { File } from "expo-file-system";

import { ApiError, api } from "../api/client";
import { ErrorBanner } from "../components/ui";
import { TOUCH_TARGET } from "../theme";
import { useApiErrorMessage } from "../useSession";

/**
 * Ponto único de entrada da transcrição: o provedor (STT sob DPA) entra AQUI.
 *
 * Contrato de privacidade (LGPD, spec §2, voz de funcionário):
 * o áudio bruto nunca é enviado para a API do PlantãoVet nem armazenado em
 * lugar nenhum; só o TEXTO revisado vai para o prontuário, via
 * `api.createShiftNote`. O arquivo local é apagado assim que esta função
 * retorna, e também se a pessoa sair da tela no meio do caminho.
 *
 * A transcrição acontece no servidor, que devolve o TEXTO sem gravar nada.
 * A revisão vem antes de o texto entrar no prontuário. Sem provedor
 * configurado a rota recusa com `transcription_unavailable`, e a pessoa digita
 * a nota: nunca inventamos texto de transcrição, porque um texto falso no
 * prontuário é risco clínico, não placeholder.
 */

/** Paleta escura da tela de gravação: a mesma do mockup AppAudio. */
const night = {
  ground: "#0F1714",
  surface: "#16211C",
  line: "#263630",
  ink: "#E6EEE9",
  ink2: "#A5B5AC",
  ink3: "#77897F",
  accent: "#46B195",
  accentSoft: "#1C332B",
  recording: "#E08379",
  barIdle: "#2F4A40",
} as const;

const BAR_COUNT = 21;

/** Nível de áudio só como affordance: a barra mostra que está vivo, não o sinal real. */
function barHeight(index: number, tick: number, active: boolean): number {
  if (!active) return 12 + ((index * 7) % 5) * 4;
  const seed = Math.sin(index * 12.9898 + tick * 4.1414) * 43758.5453;
  return 14 + Math.round((seed - Math.floor(seed)) * 64);
}

function clock(millis: number): string {
  const total = Math.max(0, Math.floor(millis / 1000));
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

type Phase = "idle" | "recording" | "transcribing" | "review";

/** Nota de plantão por voz: gravar → transcrever → revisar o texto → salvar. */
export function AudioNoteScreen({
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
  const { t } = useTranslation();
  const describeError = useApiErrorMessage();

  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const recorderState = useAudioRecorderState(recorder, 250);

  const [phase, setPhase] = useState<Phase>("idle");
  const [micDenied, setMicDenied] = useState(false);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  /** Guardado em ref para o descarte no unmount não depender de render. */
  const audioUri = useRef<string | null>(null);
  const pulse = useRef(new Animated.Value(1)).current;

  /** O arquivo local morre aqui: nunca sobe, nunca fica. */
  const discardAudio = useCallback(() => {
    const uri = audioUri.current;
    audioUri.current = null;
    if (!uri) return;
    try {
      const file = new File(uri);
      if (file.exists) file.delete();
    } catch {
      // Arquivo já sumiu (ou o SO limpou o cache): nada a fazer.
    }
  }, []);

  useEffect(() => {
    void getRecordingPermissionsAsync().then((permission) => {
      if (!permission.granted && !permission.canAskAgain) setMicDenied(true);
    });
  }, []);

  // Sair da tela descarta o áudio, gravando ou não.
  useEffect(
    () => () => {
      try {
        if (recorder.isRecording) void recorder.stop();
      } catch {
        // Recorder já liberado pelo hook.
      }
      discardAudio();
      void setAudioModeAsync({ allowsRecording: false });
    },
    [recorder, discardAudio],
  );

  useEffect(() => {
    if (phase !== "recording") {
      pulse.setValue(1);
      return;
    }
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 0.2,
          duration: 520,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 1,
          duration: 520,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ]),
    );
    animation.start();
    return () => animation.stop();
  }, [phase, pulse]);

  async function startRecording() {
    setError(null);
    try {
      let permission = await getRecordingPermissionsAsync();
      if (!permission.granted) permission = await requestRecordingPermissionsAsync();
      if (!permission.granted) {
        setMicDenied(true);
        return;
      }
      setMicDenied(false);
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      // Guarda o caminho já na largada: se a pessoa sair no meio, o arquivo some junto.
      audioUri.current = recorder.uri;
      setPhase("recording");
    } catch {
      setError(t("audioNote.recorderFailed"));
    }
  }

  async function stopAndTranscribe() {
    setPhase("transcribing");
    try {
      await recorder.stop();
      audioUri.current = recorder.uri;
      await setAudioModeAsync({ allowsRecording: false });
      const transcript = audioUri.current
        ? await api.transcribeShiftNote(hospitalizationId, audioUri.current)
        : "";
      setText((current) => (transcript ? transcript : current));
    } catch (err) {
      // Sem provedor, ou provedor fora do ar: a nota digitada continua sendo o
      // caminho. Dizer qual é o problema evita a pessoa achar que gravou errado.
      setError(
        err instanceof ApiError ? describeError(err) : t("audioNote.recorderFailed"),
      );
    } finally {
      discardAudio(); // o áudio não sobrevive à transcrição
      setPhase("review");
    }
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      // Só texto sai daqui: nenhum arquivo, nenhum blob.
      await api.createShiftNote(hospitalizationId, { text: text.trim(), source: "audio" });
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

  const tick = Math.floor(recorderState.durationMillis / 250);
  const recording = phase === "recording";

  return (
    <View style={styles.screen}>
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : undefined}
        style={{ flex: 1 }}
      >
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.header}>
            <Text style={styles.title}>{t("audioNote.title")}</Text>
            <Pressable
              onPress={onCancel}
              accessibilityRole="button"
              style={({ pressed }) => [styles.cancel, { opacity: pressed ? 0.6 : 1 }]}
            >
              <Text style={styles.cancelLabel}>{t("common.cancel")}</Text>
            </Pressable>
          </View>

          <View style={styles.patient}>
            <View style={styles.avatar}>
              <Text style={styles.avatarLabel}>
                {patientName.trim().slice(0, 1).toUpperCase() || "?"}
              </Text>
            </View>
            <View style={{ flex: 1, gap: 1 }}>
              <Text style={styles.patientName}>{patientName}</Text>
              <Text style={styles.patientMeta}>{t("audioNote.forPatient")}</Text>
            </View>
          </View>

          <ErrorBanner message={error} />

          {micDenied ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>{t("audioNote.micDeniedTitle")}</Text>
              <Text style={styles.cardText}>{t("audioNote.micDenied")}</Text>
            </View>
          ) : null}

          {phase === "idle" || recording ? (
            <View style={styles.stage}>
              <View style={styles.statusRow}>
                <Animated.View
                  style={[
                    styles.dot,
                    { backgroundColor: recording ? night.recording : night.barIdle },
                    recording ? { opacity: pulse } : null,
                  ]}
                />
                <Text style={[styles.statusLabel, recording ? styles.statusLive : null]}>
                  {recording
                    ? `${t("audioNote.recording")} · ${clock(recorderState.durationMillis)}`
                    : t("audioNote.ready")}
                </Text>
              </View>

              <View
                style={styles.bars}
                accessibilityRole="image"
                accessibilityLabel={t("audioNote.levelLabel")}
              >
                {Array.from({ length: BAR_COUNT }, (_, index) => (
                  <View
                    key={index}
                    style={[
                      styles.bar,
                      {
                        height: barHeight(index, tick, recording),
                        backgroundColor: recording ? night.accent : night.barIdle,
                      },
                    ]}
                  />
                ))}
              </View>
            </View>
          ) : null}

          {phase === "transcribing" ? (
            <View style={styles.card}>
              <Text style={styles.kicker}>{t("audioNote.transcribing")}</Text>
              <Text style={styles.cardText}>{t("audioNote.transcribingHint")}</Text>
            </View>
          ) : null}

          {phase === "review" ? (
            <View style={{ gap: 12 }}>
              <View style={{ gap: 6 }}>
                <Text style={styles.kicker}>{t("audioNote.transcript")}</Text>
                <TextInput
                  style={styles.editor}
                  value={text}
                  onChangeText={setText}
                  placeholder={t("audioNote.placeholder")}
                  placeholderTextColor={night.ink3}
                  multiline
                  textAlignVertical="top"
                  autoFocus
                />
                <Text style={styles.hint}>{t("audioNote.reviewHint")}</Text>
              </View>
            </View>
          ) : null}

          <View style={styles.card}>
            {/* LGPD, spec §2: só o texto entra no prontuário. */}
            <Text style={styles.cardText}>{t("audioNote.privacy")}</Text>
          </View>

          <View style={{ gap: 10 }}>
            {phase === "idle" ? (
              <>
                <NightButton
                  label={t("audioNote.start")}
                  onPress={() => void startRecording()}
                  disabled={micDenied}
                />
                <NightButton
                  label={t("audioNote.typeInstead")}
                  tone="ghost"
                  onPress={() => setPhase("review")}
                />
              </>
            ) : null}

            {recording ? (
              <NightButton label={t("audioNote.stop")} onPress={() => void stopAndTranscribe()} />
            ) : null}

            {phase === "review" ? (
              <>
                <NightButton
                  label={busy ? t("common.loading") : t("audioNote.save")}
                  onPress={() => void save()}
                  disabled={busy || text.trim() === ""}
                />
                <NightButton
                  label={t("audioNote.recordAgain")}
                  tone="ghost"
                  disabled={busy || micDenied}
                  onPress={() => void startRecording()}
                />
              </>
            ) : null}

            <Text style={styles.footer}>{t("audioNote.destination")}</Text>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

/** Botão da paleta escura: o PrimaryButton do design system é da paleta clara. */
function NightButton({
  label,
  onPress,
  disabled,
  tone = "primary",
}: {
  label: string;
  onPress: () => void;
  disabled?: boolean;
  tone?: "primary" | "ghost";
}) {
  const primary = tone === "primary";
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: primary ? night.accent : night.surface,
          borderColor: primary ? night.accent : night.line,
          opacity: disabled ? 0.45 : pressed ? 0.85 : 1,
        },
      ]}
    >
      <Text style={[styles.buttonLabel, { color: primary ? night.ground : night.ink }]}>
        {label}
      </Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: night.ground },
  // 54px de espaçador como nas demais telas; maxWidth centraliza no tablet.
  content: {
    padding: 20,
    paddingTop: 54,
    gap: 16,
    maxWidth: 520,
    width: "100%",
    alignSelf: "center",
  },
  header: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 },
  title: { fontSize: 20, fontWeight: "800", color: night.ink, flex: 1 },
  cancel: { minHeight: TOUCH_TARGET, justifyContent: "center", paddingHorizontal: 4 },
  cancelLabel: { fontSize: 14, fontWeight: "600", color: night.ink2 },
  patient: {
    backgroundColor: night.surface,
    borderWidth: 1,
    borderColor: night.line,
    borderRadius: 12,
    padding: 14,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  avatar: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: night.accentSoft,
    alignItems: "center",
    justifyContent: "center",
  },
  avatarLabel: { fontSize: 14, fontWeight: "700", color: night.accent },
  patientName: { fontSize: 15, fontWeight: "600", color: night.ink },
  patientMeta: { fontSize: 13, color: night.ink2 },
  stage: { alignItems: "center", gap: 18, paddingVertical: 8 },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 9, height: 9, borderRadius: 5 },
  statusLabel: { fontSize: 14, fontWeight: "600", color: night.ink2 },
  statusLive: { color: night.recording },
  bars: {
    height: 92,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 3,
  },
  bar: { width: 4, borderRadius: 2 },
  card: {
    backgroundColor: night.surface,
    borderWidth: 1,
    borderColor: night.line,
    borderRadius: 12,
    padding: 14,
    gap: 6,
  },
  cardTitle: { fontSize: 15, fontWeight: "700", color: night.ink },
  cardText: { fontSize: 13, color: night.ink2, lineHeight: 19 },
  kicker: {
    fontSize: 11,
    letterSpacing: 1,
    textTransform: "uppercase",
    fontWeight: "700",
    color: night.accent,
  },
  editor: {
    backgroundColor: night.surface,
    borderWidth: 1,
    borderColor: night.line,
    borderRadius: 12,
    padding: 14,
    minHeight: 160,
    fontSize: 16,
    lineHeight: 23,
    color: night.ink,
  },
  hint: { fontSize: 13, color: night.ink3, lineHeight: 18 },
  button: {
    minHeight: TOUCH_TARGET,
    borderRadius: 12,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 18,
  },
  buttonLabel: { fontSize: 16, fontWeight: "700" },
  footer: { fontSize: 12.5, color: night.ink3, textAlign: "center" },
});
