import { useCallback, useEffect, useRef, useState } from "react";
import { StatusBar } from "expo-status-bar";
import { SafeAreaProvider } from "react-native-safe-area-context";

import type { Task } from "./src/api/types";
import { Loading, Screen } from "./src/components/ui";
import "./src/i18n";
import { registerDevice } from "./src/notifications";
import { AudioNoteScreen } from "./src/screens/AudioNoteScreen";
import { EventScreen } from "./src/screens/EventScreen";
import { HandoverScreen } from "./src/screens/HandoverScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { PatientScreen } from "./src/screens/PatientScreen";
import { PinScreen } from "./src/screens/PinScreen";
import { ShiftScreen } from "./src/screens/ShiftScreen";
import { TaskScreen } from "./src/screens/TaskScreen";
import { SessionProvider, useSession } from "./src/useSession";

export default function App() {
  return (
    <SafeAreaProvider>
      <SessionProvider>
        <StatusBar style="dark" />
        <Router />
      </SessionProvider>
    </SafeAreaProvider>
  );
}

type Route =
  | { name: "shift" }
  | { name: "task"; task: Task }
  | { name: "handover" }
  | { name: "patient"; hospitalizationId: string; patientName: string }
  | { name: "audioNote"; hospitalizationId: string; patientName: string }
  | { name: "event"; hospitalizationId: string; patientName: string };

/** Navegação por pilha simples: o companion tem poucas telas e um fluxo só. */
function Router() {
  const { session, ready } = useSession();
  const [stack, setStack] = useState<Route[]>([{ name: "shift" }]);
  const [askPin, setAskPin] = useState(false);
  const [reload, setReload] = useState(0);
  const pushRegistered = useRef(false);

  const current = stack[stack.length - 1];
  const push = useCallback((route: Route) => setStack((s) => [...s, route]), []);
  const pop = useCallback(
    () => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s)),
    [],
  );
  const home = useCallback(() => {
    setStack([{ name: "shift" }]);
    setReload((value) => value + 1);
  }, []);

  useEffect(() => {
    // Alerta no bolso só faz sentido depois que há sessão.
    if (!session || pushRegistered.current) return;
    pushRegistered.current = true;
    // Registrar no SERVIDOR, não só pedir permissão: o token era obtido e
    // jogado fora, então o app pedia autorização de notificação à pessoa e
    // nunca conseguia notificá-la.
    void registerDevice();
  }, [session]);

  if (!ready) {
    return (
      <Screen>
        <Loading />
      </Screen>
    );
  }

  if (!session) return <LoginScreen />;

  if (askPin) {
    return (
      <PinScreen
        context={current.name === "task" ? current.task.title : undefined}
        onDone={() => setAskPin(false)}
        onCancel={() => setAskPin(false)}
      />
    );
  }

  switch (current.name) {
    case "task":
      return (
        <TaskScreen
          task={current.task}
          onBack={pop}
          onNeedsOperator={() => setAskPin(true)}
          onDone={home}
        />
      );

    case "handover":
      return <HandoverScreen onBack={pop} onNeedsOperator={() => setAskPin(true)} />;

    case "patient":
      return (
        <PatientScreen
          hospitalizationId={current.hospitalizationId}
          onBack={pop}
          onOpenTask={(task) => push({ name: "task", task })}
          onAddNote={() =>
            push({
              name: "audioNote",
              hospitalizationId: current.hospitalizationId,
              patientName: current.patientName,
            })
          }
          onAddEvent={() =>
            push({
              name: "event",
              hospitalizationId: current.hospitalizationId,
              patientName: current.patientName,
            })
          }
        />
      );

    case "audioNote":
      return (
        <AudioNoteScreen
          hospitalizationId={current.hospitalizationId}
          patientName={current.patientName}
          onDone={pop}
          onCancel={pop}
          onNeedsOperator={() => setAskPin(true)}
        />
      );

    case "event":
      return (
        <EventScreen
          hospitalizationId={current.hospitalizationId}
          patientName={current.patientName}
          onDone={pop}
          onCancel={pop}
          onNeedsOperator={() => setAskPin(true)}
        />
      );

    default:
      return (
        <ShiftScreen
          key={reload}
          onOpenTask={(task) => push({ name: "task", task })}
          onOpenHandover={() => push({ name: "handover" })}
          onOpenPatient={(hospitalizationId, patientName) =>
            push({ name: "patient", hospitalizationId, patientName })
          }
        />
      );
  }
}
