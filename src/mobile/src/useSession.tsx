import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, loadSession, saveSession } from "./api/client";
import type { Session } from "./api/client";

interface SessionContextValue {
  session: Session | null;
  ready: boolean;
  loginPersonal: (email: string, password: string) => Promise<void>;
  loginStation: (clinicSlug: string, stationKey: string) => Promise<void>;
  identifyOperator: (pin: string) => Promise<void>;
  logout: () => Promise<void>;
  needsOperator: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    void loadSession().then((stored) => {
      setSession(stored);
      setReady(true);
    });
  }, []);

  const persist = useCallback(async (next: Session | null) => {
    await saveSession(next);
    setSession(next);
  }, []);

  const value = useMemo<SessionContextValue>(
    () => ({
      session,
      ready,
      needsOperator: session?.kind === "station" && !session.operatorToken,
      loginPersonal: async (email, password) => {
        const token = await api.login(email, password);
        await persist({ kind: "personal", accessToken: token.access_token });
      },
      loginStation: async (clinicSlug, stationKey) => {
        const token = await api.stationLogin(clinicSlug, stationKey);
        await persist({ kind: "station", accessToken: token.access_token });
      },
      identifyOperator: async (pin) => {
        if (!session) return;
        const result = await api.exchangePin(pin);
        await persist({ ...session, operatorToken: result.operator_token });
      },
      logout: () => persist(null),
    }),
    [session, ready, persist],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession precisa do SessionProvider");
  return context;
}

/** Traduz o código de erro da API: a API nunca manda prosa. */
export function useApiErrorMessage() {
  const { t } = useTranslation();
  // Estável entre renders: as telas usam isto em dependência de efeito, e uma
  // função nova a cada render vira laço infinito de requisições.
  return useCallback((error: unknown): string => {
    const code = (error as { code?: string })?.code;
    if (code) {
      return t(`error.${code}`, {
        ...((error as { params?: Record<string, unknown> }).params ?? {}),
        defaultValue: t("error.unknown_error"),
      });
    }
    return t("error.unknown_error");
  }, [t]);
}
