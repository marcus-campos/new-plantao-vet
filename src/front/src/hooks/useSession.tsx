import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  ApiError,
  api,
  loadDevice,
  loadSession,
  saveDevice,
  saveSession,
  setUnauthorizedHandler,
} from "../api/client";
import type { DeviceCredential, Session } from "../api/client";
import type { Me, Operator, PlatformMe } from "../api/types";

interface SessionContextValue {
  session: Session | null;
  loginPersonal: (email: string, password: string) => Promise<void>;
  /** A porta de quem vende e dá suporte. Outro token, nenhuma clínica. */
  loginPlatform: (email: string, password: string) => Promise<void>;
  /** Quem está logado na plataforma. Null fora dela. */
  platformUser: PlatformMe | null;
  /** Caminho antigo: a senha única da clínica. Continua aqui enquanto houver
   *  aparelho em campo que só conhece ela. */
  loginStation: (clinicSlug: string, stationKey: string) => Promise<void>;
  /** Primeira entrada de um aparelho: troca o código de seis dígitos que o
   *  administrador leu na tela dele pelo segredo próprio, e já entra. */
  enrollStation: (clinicSlug: string, code: string, deviceName: string) => Promise<void>;
  /** Entra com a credencial que ESTE aparelho já guarda. */
  loginWithDevice: () => Promise<void>;
  /** Este aparelho, quando já foi liberado alguma vez. */
  device: DeviceCredential | null;
  /** Esquece a credencial guardada: "este não é mais o tablet da UTI". */
  forgetDevice: () => void;
  /** Modo estação: troca o PIN por um operator token de 5 min. */
  identifyOperator: (pin: string) => Promise<void>;
  clearOperator: () => void;
  logout: () => void;
  /** true quando a próxima mutação clínica vai exigir PIN. */
  needsOperator: boolean;
  /** Quem está logado. Null enquanto carrega. */
  me: Me | null;
  /** Na estação: quem digitou o PIN. Null quando ninguém se identificou. */
  operator: Operator | null;
  /** O papel de quem responde pelos atos agora: o vínculo, ou o dono do PIN. */
  role: Me["role"];
  /** O nome de quem está identificado, para a interface dizer por quem age. */
  actorName: string | null;
  /** Esconde o que a API recusaria: botão que devolve 403 é pior que ausente.
   *
   *  Na estação a resposta vem do OPERADOR, não do aparelho. Antes devolvia
   *  `true` para tudo. O aparelho não tem papel próprio (`/auth/me` responde
   *  papel nulo de propósito), e o cliente lia isso como "posso tudo": a
   *  navegação de gestão inteira aparecia num tablet do corredor, e o técnico
   *  escrevia uma evolução completa para receber 403 depois de digitar o PIN.
   *
   *  Sem ninguém identificado na estação, `can` é falso para tudo: a interface
   *  pede o PIN em vez de oferecer o que talvez não possa. */
  can: (capability: string) => boolean;
  /** Ainda buscando quem é. Evita a tela piscar sem nenhuma ação enquanto
   *  `/auth/me` não voltou, o que fazia o app parecer simplesmente vazio. */
  loading: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [me, setMe] = useState<Me | null>(null);
  const [operator, setOperator] = useState<Operator | null>(null);
  const [loading, setLoading] = useState(true);
  const [device, setDevice] = useState<DeviceCredential | null>(() => loadDevice());
  const [platformUser, setPlatformUser] = useState<PlatformMe | null>(null);

  const persist = useCallback((next: Session | null) => {
    saveSession(next);
    setSession(next);
    if (next === null) {
      setMe(null);
      setOperator(null);
      setPlatformUser(null);
    }
  }, []);

  // Sessão recusada pela API: sai do caminho em vez de deixar a pessoa presa
  // numa tela morta clicando em botões que só devolvem erro.
  useEffect(() => {
    setUnauthorizedHandler(() => persist(null));
    return () => setUnauthorizedHandler(null);
  }, [persist]);

  // Carrega uma vez por sessão. A dependência é o TOKEN, não o objeto sessão:
  // trocar o operator token não pode redisparar isto (foi assim que a tela
  // entrou em laço de requisições antes).
  const accessToken = session?.accessToken;
  const sessionKind = session?.kind;
  useEffect(() => {
    if (!accessToken) {
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    // A plataforma não tem `/auth/me`: o token dela é recusado por toda rota
    // de clínica, de propósito. Quem ela é vem de `/platform/me`.
    if (sessionKind === "platform") {
      void api
        .platformMe()
        .then((value) => {
          if (alive) setPlatformUser(value);
        })
        .catch(() => undefined)
        .finally(() => {
          if (alive) setLoading(false);
        });
      return () => {
        alive = false;
      };
    }
    void api
      .me()
      .then((value) => {
        if (alive) setMe(value);
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [accessToken, sessionKind]);

  // Quem digitou o PIN. Só na estação: no modo pessoal o vínculo já responde.
  const operatorToken = session?.operatorToken;
  useEffect(() => {
    if (session?.kind !== "station" || !operatorToken) {
      setOperator(null);
      return;
    }
    let alive = true;
    void api
      .operator()
      .then((value) => {
        if (alive) setOperator(value);
      })
      .catch(() => {
        if (alive) setOperator(null);
      });
    return () => {
      alive = false;
    };
  }, [session?.kind, operatorToken]);

  const value = useMemo<SessionContextValue>(() => {
    const station = session?.kind === "station";
    const capabilities = station ? (operator?.capabilities ?? []) : (me?.capabilities ?? []);
    return {
      session,
      me,
      operator,
      loading,
      role: station ? (operator?.role ?? null) : (me?.role ?? null),
      actorName: station ? (operator?.name ?? null) : null,
      needsOperator: station && !session.operatorToken,
      can: (capability) => capabilities.includes(capability),
      loginPersonal: async (email, password) => {
        const token = await api.login(email, password);
        persist({ kind: "personal", accessToken: token.access_token });
      },
      loginPlatform: async (email, password) => {
        const token = await api.platformLogin(email, password);
        persist({ kind: "platform", accessToken: token.access_token });
      },
      platformUser,
      loginStation: async (clinicSlug, stationKey) => {
        const token = await api.stationLoginWithKey(clinicSlug, stationKey);
        persist({ kind: "station", accessToken: token.access_token });
      },
      enrollStation: async (clinicSlug, code, deviceName) => {
        const enrolled = await api.enrollDevice(clinicSlug, code, deviceName);
        const credential: DeviceCredential = {
          clinicSlug,
          deviceId: enrolled.device_id,
          deviceSecret: enrolled.device_secret,
          deviceName: enrolled.device_name,
        };
        // Guarda ANTES de entrar: o segredo sai da API uma vez só, e perdê-lo
        // por causa de uma falha de rede no login custaria outra liberação.
        saveDevice(credential);
        setDevice(credential);
        const token = await api.stationLogin(clinicSlug, credential.deviceId, credential.deviceSecret);
        persist({ kind: "station", accessToken: token.access_token });
      },
      loginWithDevice: async () => {
        if (!device) return;
        try {
          const token = await api.stationLogin(
            device.clinicSlug,
            device.deviceId,
            device.deviceSecret,
          );
          persist({ kind: "station", accessToken: token.access_token });
        } catch (err) {
          // Aparelho revogado: guardar uma credencial morta faria a tela
          // oferecer "entrar como Tablet da UTI" para sempre, e falhar sempre.
          if (err instanceof ApiError && err.status === 401) {
            saveDevice(null);
            setDevice(null);
          }
          throw err;
        }
      },
      device,
      forgetDevice: () => {
        saveDevice(null);
        setDevice(null);
      },
      identifyOperator: async (pin) => {
        if (!session) return;
        // saveSession antes da chamada não: o token só vale se o PIN passar.
        const result = await api.exchangePin(pin);
        persist({ ...session, operatorToken: result.operator_token });
      },
      clearOperator: () => {
        if (session) persist({ ...session, operatorToken: undefined });
      },
      logout: () => persist(null),
    };
  }, [session, me, operator, loading, device, platformUser, persist]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession precisa do SessionProvider");
  return context;
}
