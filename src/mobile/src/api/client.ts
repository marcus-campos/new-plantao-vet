import AsyncStorage from "@react-native-async-storage/async-storage";

import type {
  Board,
  HandoverReport,
  HospitalizationDetail,
  Me,
  Page,
  ShiftNote,
  Task,
  TokenResponse,
} from "./types";

/** Em dev aponte para o IP da máquina na rede: o device não enxerga localhost. */
const BASE_URL = process.env.EXPO_PUBLIC_API_URL ?? "http://localhost:8000";

const SESSION_KEY = "plantaovet.session";

export interface Session {
  kind: "personal" | "station";
  accessToken: string;
  /** Modo estação: token de 5 min obtido pelo PIN, exigido nas mutações clínicas. */
  operatorToken?: string;
}

let cached: Session | null = null;

export async function loadSession(): Promise<Session | null> {
  if (cached) return cached;
  const raw = await AsyncStorage.getItem(SESSION_KEY);
  cached = raw ? (JSON.parse(raw) as Session) : null;
  return cached;
}

export async function saveSession(session: Session | null): Promise<void> {
  cached = session;
  if (session) await AsyncStorage.setItem(SESSION_KEY, JSON.stringify(session));
  else await AsyncStorage.removeItem(SESSION_KEY);
}

/** A API devolve código estável, nunca prosa (ADR-0004). */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly params: Record<string, unknown>;

  constructor(code: string, status: number, params: Record<string, unknown> = {}) {
    super(code);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.params = params;
  }
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  needsOperator?: boolean;
  query?: Record<string, string | undefined>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = await loadSession();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (session) headers.authorization = `Bearer ${session.accessToken}`;
  if (options.needsOperator && session?.operatorToken) {
    headers["X-Operator-Token"] = session.operatorToken;
  }

  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) query.set(key, value);
  }
  const suffix = query.toString() ? `?${query.toString()}` : "";

  const response = await fetch(`${BASE_URL}${path}${suffix}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const payload = text ? JSON.parse(text) : null;

  if (!response.ok) {
    throw new ApiError(
      payload?.error?.code ?? "unknown_error",
      response.status,
      payload?.error?.params ?? {},
    );
  }
  return payload as T;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("/api/v1/auth/login", { method: "POST", body: { email, password } }),

  stationLogin: (clinicSlug: string, stationKey: string) =>
    request<TokenResponse>("/api/v1/auth/station", {
      method: "POST",
      body: { clinic_slug: clinicSlug, station_key: stationKey },
    }),

  exchangePin: (pin: string) =>
    request<{ operator_token: string }>("/api/v1/auth/pin", { method: "POST", body: { pin } }),

  me: () => request<Me>("/api/v1/auth/me"),

  tasks: (from?: string, to?: string, cursor?: string) =>
    request<Page<Task>>("/api/v1/tasks", { query: { from, to, cursor } }),

  /** A fila INTEIRA do turno, seguindo o cursor até o fim.
   *
   *  Uma página só engana: numa clínica de 25 leitos o turno passa de 50
   *  tarefas, e o plantonista não pode deixar de ver metade das doses. */
  allTasks: async (from?: string, to?: string): Promise<Task[]> => {
    const todas: Task[] = [];
    let cursor: string | undefined;
    // Trava de segurança: cursor com defeito não pode virar laço infinito.
    for (let pagina = 0; pagina < 40; pagina += 1) {
      const page = await api.tasks(from, to, cursor);
      todas.push(...page.items);
      if (!page.next_cursor) break;
      cursor = page.next_cursor;
    }
    return todas;
  },

  board: () => request<Board>("/api/v1/board"),

  hospitalization: (id: string) =>
    request<HospitalizationDetail>(`/api/v1/hospitalizations/${id}`),

  executeTask: (id: string, body: Record<string, unknown> = {}) =>
    request<Task>(`/api/v1/tasks/${id}/execute`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  notDoneTask: (id: string, reason: string, values?: Record<string, unknown>) =>
    request<Task>(`/api/v1/tasks/${id}/not-done`, {
      method: "POST",
      body: { reason, values },
      needsOperator: true,
    }),

  adHocTask: (body: Record<string, unknown>) =>
    request<Task>("/api/v1/tasks/ad-hoc", { method: "POST", body, needsOperator: true }),

  shiftNotes: (hospitalizationId: string) =>
    request<ShiftNote[] | Page<ShiftNote>>(
      `/api/v1/hospitalizations/${hospitalizationId}/shift-notes`,
    ),

  /** O aparelho passa a receber o plantão de quem está logado nele. */
  registerDevice: (token: string, platform: string) =>
    request<{ id: string }>("/api/v1/devices", {
      method: "POST",
      body: { token, platform },
    }),

  unregisterDevice: (token: string) =>
    request<void>(`/api/v1/devices/${encodeURIComponent(token)}`, { method: "DELETE" }),

  /** Manda o áudio e recebe o TEXTO, sem gravar nada.
   *
   *  `transcribe()` devolvia string vazia: a pessoa gravava, via
   *  "Transcrevendo…" e recebia um campo em branco para digitar.
   *
   *  Devolve texto em vez de criar a nota porque a revisão vem ANTES de gravar:
   *  a transcrição erra e o prontuário é append-only, então corrigir depois
   *  vira adendo: registro de erro em vez de prevenção. A spec diz que o áudio
   *  é apagado depois da transcrição *confirmada*, e confirmar é alguém ler.
   *
   *  O áudio não é guardado em lugar nenhum: só o texto entra no prontuário
   *  (LGPD, voz de funcionário). */
  transcribeShiftNote: async (
    hospitalizationId: string,
    uri: string,
    mimeType = "audio/m4a",
  ) => {
    const session = await loadSession();
    const form = new FormData();
    // O React Native monta o multipart a partir deste objeto; o tipo explícito
    // é obrigatório: o servidor recusa `application/octet-stream`.
    form.append("audio", { uri, name: "nota.m4a", type: mimeType } as unknown as Blob);
    const headers: Record<string, string> = {};
    if (session) headers.authorization = `Bearer ${session.accessToken}`;
    if (session?.operatorToken) headers["X-Operator-Token"] = session.operatorToken;
    const response = await fetch(
      new URL(`/api/v1/hospitalizations/${hospitalizationId}/shift-notes/transcribe`, BASE_URL),
      { method: "POST", headers, body: form },
    );
    const text = await response.text();
    let payload: any = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      /* proxy fora do ar devolve HTML */
    }
    if (!response.ok) {
      throw new ApiError(payload?.error?.code ?? "unknown_error", response.status);
    }
    return (payload as { text: string }).text;
  },

  createShiftNote: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<ShiftNote>(`/api/v1/hospitalizations/${hospitalizationId}/shift-notes`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  handoverReports: (shiftId?: string) =>
    request<HandoverReport[] | Page<HandoverReport>>("/api/v1/handover/reports", {
      query: { shift_id: shiftId },
    }),

  ackHandover: (reportId: string, secondsToAck: number) =>
    request<unknown>(`/api/v1/handover/reports/${reportId}/ack`, {
      method: "POST",
      body: { seconds_to_ack: secondsToAck },
      needsOperator: true,
    }),

  approveHandover: (reportId: string) =>
    request<HandoverReport>(`/api/v1/handover/reports/${reportId}/approve`, {
      method: "POST",
      needsOperator: true,
    }),
};

/** Rotas devolvem lista pura ou Page; normaliza para array. */
export function asList<T>(value: T[] | Page<T> | undefined | null): T[] {
  if (!value) return [];
  return Array.isArray(value) ? value : value.items;
}
