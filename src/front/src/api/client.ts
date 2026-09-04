import type {
  AuditEntry,
  Board,
  ClinicProfile,
  ClinicSettings,
  ComplianceAlerts,
  DosePreview,
  DoseRule,
  HandoverReport,
  Hospitalization,
  HospitalizationDetail,
  Kennel,
  MedicalRecord,
  MembershipRoster,
  MembershipRow,
  Me,
  Operator,
  Owner,
  OwnerContact,
  Page,
  Patient,
  PatientSearchHit,
  Plan,
  PlatformClinic,
  PlatformClinicRow,
  PlatformMe,
  Prescription,
  PriceListItem,
  SchedulePreview,
  ProgressNote,
  Shift,
  ShiftNote,
  StationDevice,
  Statement,
  Task,
  TokenResponse,
} from "./types";

// Em produção o build passa `VITE_API_URL=` VAZIO de propósito: o web e a API
// vivem na mesma origem, e o Caddy roteia /api. Mas `??` só troca null e
// undefined — string vazia passa direto —, e `new URL(path, "")` lança
// `TypeError: Invalid URL`. O resultado era nenhuma chamada de API funcionar
// pelo navegador em produção, com a tela mostrando "Algo deu errado".
//
// `||` em vez de `??` porque aqui a string vazia É um caso a tratar, e a
// origem da página é a base certa quando ninguém informou outra.
const BASE_URL =
  import.meta.env.VITE_API_URL ||
  (typeof window === "undefined" ? "http://localhost:8000" : window.location.origin);

const SESSION_KEY = "plantaovet.session";

export interface Session {
  /** Modo pessoal: o profissional na própria conta. Estação: dispositivo da
   *  clínica. Plataforma: quem vende e dá suporte, fora de qualquer clínica. */
  kind: "personal" | "station" | "platform";
  accessToken: string;
  /** Só no modo estação: token de 5 min obtido por PIN, exigido nas mutações. */
  operatorToken?: string;
  operatorName?: string;
}

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session | null): void {
  try {
    if (session) localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* navegador sem storage: a sessão vive só em memória nesta aba */
  }
}

const DEVICE_KEY = "plantaovet.device";

/** A credencial DESTE aparelho, guardada depois da liberação.
 *
 *  É o que faz o tablet do corredor voltar a funcionar sozinho quando alguém
 *  o liga: o segredo é dele, e sair (ou o token de 12h expirar) não obriga
 *  ninguém a procurar um administrador de novo. A chave de estação exigia
 *  redigitar um segredo que circulava por aí. */
export interface DeviceCredential {
  clinicSlug: string;
  deviceId: string;
  deviceSecret: string;
  deviceName: string;
}

export function loadDevice(): DeviceCredential | null {
  try {
    const raw = localStorage.getItem(DEVICE_KEY);
    return raw ? (JSON.parse(raw) as DeviceCredential) : null;
  } catch {
    return null;
  }
}

export function saveDevice(device: DeviceCredential | null): void {
  try {
    if (device) localStorage.setItem(DEVICE_KEY, JSON.stringify(device));
    else localStorage.removeItem(DEVICE_KEY);
  } catch {
    /* navegador sem storage: este aparelho pede o código a cada vez */
  }
}

/** Avisado quando a API recusa a sessão.
 *
 *  Sem isto o 401 não tinha saída: a tradução dizia "Entre de novo" e o usuário
 *  ficava preso numa tela com sessão morta, vendo "Algo deu errado" em cada
 *  ação. Quem escuta é o SessionProvider, que derruba a sessão e volta ao login.
 */
let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

/** Erro da API: a resposta traz código estável, nunca prosa (ADR-0004). */
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
  /** Mutação clínica: no modo estação exige o operator token do PIN. */
  needsOperator?: boolean;
  query?: Record<string, string | number | undefined>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const session = loadSession();
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (session) headers.authorization = `Bearer ${session.accessToken}`;
  if (options.needsOperator && session?.operatorToken) {
    headers["X-Operator-Token"] = session.operatorToken;
  }

  const url = new URL(path, BASE_URL);
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }

  const response = await fetch(url, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  // Um proxy fora do ar devolve HTML: sem a guarda, `JSON.parse` lança
  // SyntaxError e a tela mostra "Algo deu errado", sem distinguir falha de
  // infraestrutura de erro de negócio.
  let payload: { error?: { code?: string; params?: Record<string, unknown> } } | null = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    if (response.ok) throw new ApiError("unknown_error", response.status);
  }

  if (!response.ok) {
    const code = payload?.error?.code ?? "unknown_error";
    if (response.status === 401) onUnauthorized?.();
    throw new ApiError(code, response.status, payload?.error?.params ?? {});
  }
  return payload as T;
}

export interface SignupPayload {
  clinic_name: string;
  admin_name: string;
  email: string;
  password: string;
  phone?: string;
}

export const api = {
  login: (email: string, password: string) =>
    request<TokenResponse>("/api/v1/auth/login", {
      method: "POST",
      body: { email, password },
    }),

  /** A porta pública: cria a clínica e já devolve a sessão. */
  signup: (payload: SignupPayload) =>
    request<TokenResponse>("/api/v1/signup", { method: "POST", body: payload }),

  /** Caminho antigo: a senha única da clínica. Mantido enquanto houver
   *  aparelho em campo que só conhece ela. */
  stationLoginWithKey: (clinicSlug: string, stationKey: string) =>
    request<TokenResponse>("/api/v1/auth/station", {
      method: "POST",
      body: { clinic_slug: clinicSlug, station_key: stationKey },
    }),

  /** Entrada do aparelho já liberado. O segredo é dele, não da clínica:
   *  revogar este aparelho não derruba os outros. */
  stationLogin: (clinicSlug: string, deviceId: string, deviceSecret: string) =>
    request<TokenResponse>("/api/v1/auth/station", {
      method: "POST",
      body: { clinic_slug: clinicSlug, device_id: deviceId, device_secret: deviceSecret },
    }),

  /** Primeira entrada: o aparelho troca o código de seis dígitos que o
   *  administrador leu na tela dele pelo próprio segredo. */
  enrollDevice: (clinicSlug: string, code: string, deviceName: string) =>
    request<{ device_id: string; device_secret: string; device_name: string }>(
      "/api/v1/auth/station/enroll",
      { method: "POST", body: { clinic_slug: clinicSlug, code, device_name: deviceName } },
    ),

  exchangePin: (pin: string) =>
    request<{ operator_token: string }>("/api/v1/auth/pin", {
      method: "POST",
      body: { pin },
    }),

  me: () => request<Me>("/api/v1/auth/me"),

  /** O que pode quem está com o dedo no aparelho agora.
   *
   *  No modo estação a resposta depende do PIN: sem ele a API devolve
   *  `operator_required` e a interface pede identificação em vez de oferecer o
   *  que a pessoa não pode fazer. */
  operator: () =>
    request<Operator>("/api/v1/auth/operator", { needsOperator: true }),

  board: () => request<Board>("/api/v1/board"),

  tasks: (from?: string, to?: string, limit = 200) =>
    request<Page<Task>>("/api/v1/tasks", { query: { from, to, limit } }),

  /** As internações de um paciente, da mais recente para a mais antiga.
   *
   *  Dar alta fazia o paciente sumir: o painel só lista quem está internado
   *  AGORA, e a busca só devolvia a internação ativa, então quem acabava de
   *  dar alta não tinha caminho de volta para a conta nem para o prontuário, que
   *  é exatamente o que se faz depois. */
  hospitalizations: (patientId: string) =>
    request<Hospitalization[]>("/api/v1/hospitalizations", {
      query: { patient_id: patientId },
    }),

  hospitalization: (id: string) =>
    request<HospitalizationDetail>(`/api/v1/hospitalizations/${id}`),

  executeTask: (
    id: string,
    body: {
      values?: Record<string, unknown>;
      retroactive?: boolean;
      performed_at?: string;
      partial?: boolean;
      confirm_early?: boolean;
    } = {},
  ) =>
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

  adHocTask: (body: {
    prescription_id?: string;
    hospitalization_id?: string;
    title?: string;
    category?: string;
    values?: Record<string, unknown>;
    override?: boolean;
  }) =>
    request<Task>("/api/v1/tasks/ad-hoc", {
      method: "POST",
      body,
      needsOperator: true,
    }),

  /** A dose já calculada para o paciente desta internação.
   *
   *  O sistema tem as duas coisas que faltavam: a concentração da apresentação
   *  e o peso e a espécie do paciente. Perguntar de novo o que ele já sabe é
   *  pedir ao veterinário uma conta que a máquina faz certo. */
  previewDose: (body: {
    price_list_item_id: string;
    hospitalization_id: string;
    dose_per_kg?: string | null;
  }) => request<DosePreview>("/api/v1/prescriptions/dose-preview", { method: "POST", body }),

  /** Os horários que a prescrição VAI criar, calculados pelo servidor.
   *
   *  O preview era recalculado no cliente, com uma tabela de âncoras cravada no
   *  código, enquanto Configurações deixa a clínica editar as dela. A tela
   *  prometia horários que o servidor não ia criar. */
  previewSchedule: (body: Record<string, unknown>) =>
    request<SchedulePreview>("/api/v1/prescriptions/preview", { method: "POST", body }),

  createPrescription: (
    hospitalizationId: string,
    body: Record<string, unknown>,
  ) =>
    request<Prescription>(
      `/api/v1/hospitalizations/${hospitalizationId}/prescriptions`,
      { method: "POST", body, needsOperator: true },
    ),

  /** Titulação: nova VERSÃO ligada à anterior por `replaces_prescription_id`.
   *
   *  Suspender e recriar quebraria a cronologia da prescrição na auditoria,
   *  que é justamente o que a titulação de fluido precisa preservar. */
  adjustPrescription: (id: string, body: Record<string, unknown>) =>
    request<Prescription>(`/api/v1/prescriptions/${id}/adjust`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  suspendPrescription: (id: string) =>
    request<Prescription>(`/api/v1/prescriptions/${id}/suspend`, {
      method: "POST",
      needsOperator: true,
    }),

  patients: () => request<Page<Patient>>("/api/v1/patients"),
  patient: (id: string) => request<Patient>(`/api/v1/patients/${id}`),

  /** Caixa única de busca: nome do paciente, identificador (microchip, CPF…),
   *  nome ou documento do responsável. Devolve lista, não página. */
  searchPatients: (q: string) =>
    request<PatientSearchHit[]>("/api/v1/patients/search", { query: { q } }),

  /** Cadastro em um passo: paciente + responsável + identificadores. */
  registerPatient: (body: Record<string, unknown>) =>
    request<Patient>("/api/v1/patients/register", {
      method: "POST",
      body,
      needsOperator: true,
    }),

  /** Perfil de compliance: diz quais identificadores esta clínica usa. */
  clinicProfile: () => request<ClinicProfile>("/api/v1/clinic/profile"),

  /** As áreas que a clínica pode escolher nas configurações. */
  clinicProfiles: () => request<ClinicProfile[]>("/api/v1/clinic/profiles"),
  kennels: (includeInactive = false) =>
    request<Page<Kennel>>("/api/v1/kennels", {
      // Sem isso não há como reativar um box desativado: ele some do mapa.
      query: { include_inactive: includeInactive ? "true" : undefined, limit: 200 },
    }),
  owners: () => request<Page<Owner>>("/api/v1/owners"),

  createHospitalization: (body: Record<string, unknown>) =>
    request<{ hospitalization: { id: string }; warning: string | null }>(
      "/api/v1/hospitalizations",
      { method: "POST", body, needsOperator: true },
    ),

  hospitalizationOutcome: (
    id: string,
    body: { outcome: string; note?: string; confirm_pending_tasks?: boolean },
  ) =>
    request<{ id: string; status: string }>(`/api/v1/hospitalizations/${id}/outcome`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  createOwner: (body: { name: string; phone_e164: string; tax_id?: string }) =>
    request<Owner>("/api/v1/owners", { method: "POST", body, needsOperator: true }),

  updateOwner: (id: string, body: Record<string, unknown>) =>
    request<Owner>(`/api/v1/owners/${id}`, { method: "PATCH", body, needsOperator: true }),

  /** Registra o aceite do tutor em receber mensagens (exigência da Meta e da LGPD). */
  setWhatsAppOptIn: (ownerId: string) =>
    request<Owner>(`/api/v1/owners/${ownerId}`, {
      method: "PATCH",
      body: { whatsapp_opt_in_at: new Date().toISOString() },
      needsOperator: true,
    }),

  createPatient: (body: Record<string, unknown>) =>
    request<Patient>("/api/v1/patients", { method: "POST", body, needsOperator: true }),

  createKennel: (body: { name: string; area?: string }) =>
    request<Kennel>("/api/v1/kennels", { method: "POST", body, needsOperator: true }),

  updateKennel: (id: string, body: Record<string, unknown>) =>
    request<Kennel>(`/api/v1/kennels/${id}`, { method: "PATCH", body, needsOperator: true }),

  /* ---- conta e preços ---- */
  statement: (hospitalizationId: string) =>
    request<Statement>(`/api/v1/hospitalizations/${hospitalizationId}/charges`),

  addManualCharge: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<unknown>(`/api/v1/hospitalizations/${hospitalizationId}/charges`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  // `needsOperator` numa LEITURA: a tabela de preços passou a exigir capacidade
  // (curadoria, prescrição ou lançamento na conta), e na estação quem responde
  // pelo acesso é o dono do PIN. Sem mandar o token do operador, o tablet
  // receberia `operator_required` mesmo com alguém já identificado, e o preço
  // pararia de aparecer sozinho na hora de prescrever.
  priceList: (includeInactive = false) =>
    request<Page<PriceListItem>>("/api/v1/price-list", {
      query: { include_inactive: includeInactive ? "true" : undefined, limit: 200 },
      needsOperator: true,
    }),

  createPriceListItem: (body: Record<string, unknown>) =>
    request<PriceListItem>("/api/v1/price-list", { method: "POST", body, needsOperator: true }),

  updatePriceListItem: (id: string, body: Record<string, unknown>) =>
    request<PriceListItem>(`/api/v1/price-list/${id}`, {
      method: "PATCH",
      body,
      needsOperator: true,
    }),

  /** A posologia do item, por espécie. Uma leitura só: quem administra a tabela
   *  e quem prescreve leem a MESMA regra que a calculadora usa. */
  doseRules: (itemId: string) => request<DoseRule[]>(`/api/v1/price-list/${itemId}/dose-rules`),

  saveDoseRule: (itemId: string, body: Record<string, unknown>) =>
    request<DoseRule>(`/api/v1/price-list/${itemId}/dose-rules`, {
      method: "PUT",
      body,
      needsOperator: true,
    }),

  /* ---- evolução e prontuário ---- */
  progressNotes: (hospitalizationId: string) =>
    request<ProgressNote[]>(`/api/v1/hospitalizations/${hospitalizationId}/progress-notes`),

  createProgressNote: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<ProgressNote>(`/api/v1/hospitalizations/${hospitalizationId}/progress-notes`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  complianceAlerts: () => request<ComplianceAlerts>("/api/v1/compliance/alerts"),

  /** O PDF do prontuário, gerado no servidor.
   *
   *  Vem pelo cliente para a base da API e o cabeçalho de autorização morarem
   *  num lugar só. A tela relia `VITE_API_URL` por conta. */
  medicalRecordPdf: async (hospitalizationId: string, include: string): Promise<Blob> => {
    const session = loadSession();
    const url = new URL(`/api/v1/hospitalizations/${hospitalizationId}/record.pdf`, BASE_URL);
    // Sempre enviado, inclusive vazio: "nenhuma seção" é um pedido, e omitir o
    // parâmetro faria o servidor devolver o default: o papel traria seções que
    // a pessoa desmarcou na tela.
    url.searchParams.set("include", include);
    const headers: Record<string, string> = {};
    if (session) headers.authorization = `Bearer ${session.accessToken}`;
    // Leitura sensível: na estação quem responde pelo acesso é o dono do PIN.
    if (session?.operatorToken) headers["X-Operator-Token"] = session.operatorToken;
    const response = await fetch(url, { headers });
    if (!response.ok) {
      let code = "unknown_error";
      try {
        code = (await response.json())?.error?.code ?? code;
      } catch {
        // corpo não-JSON (proxy fora do ar): o código genérico já descreve
      }
      throw new ApiError(code, response.status);
    }
    return response.blob();
  },

  medicalRecord: (hospitalizationId: string, include?: string) =>
    request<MedicalRecord>(`/api/v1/hospitalizations/${hospitalizationId}/record`, {
      query: { include },
    }),

  /* ---- plantão e passagem ---- */
  shifts: (query: { from?: string; to?: string; limit?: number } = {}) =>
    request<Shift[] | Page<Shift>>("/api/v1/shifts", { query }),

  createShift: (body: Record<string, unknown>) =>
    request<Shift>("/api/v1/shifts", { method: "POST", body, needsOperator: true }),

  closeShift: (id: string, body: Record<string, unknown> = {}) =>
    request<{ missing_review?: string[] }>(`/api/v1/shifts/${id}/close`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  handoverReports: (shiftId?: string) =>
    request<HandoverReport[] | Page<HandoverReport>>("/api/v1/handover/reports", {
      query: { shift_id: shiftId },
    }),

  /** Escreve o boletim.
   *
   *  Sem `text`, o servidor rascunha. Com `text`, grava o que a pessoa escreveu.
   *  A rota só sabia gerar, então quem entrega o plantão não tinha como
   *  corrigir uma frase nem redigir o boletim do próprio punho. Rascunho é
   *  rascunho porque quem assina é a pessoa. */
  setNarrative: (reportId: string, text?: string) =>
    request<HandoverReport>(`/api/v1/handover/reports/${reportId}/narrative`, {
      method: "POST",
      body: { text: text ?? null },
      needsOperator: true,
    }),

  approveHandover: (reportId: string) =>
    request<HandoverReport>(`/api/v1/handover/reports/${reportId}/approve`, {
      method: "POST",
      needsOperator: true,
    }),

  ackHandover: (reportId: string, secondsToAck: number) =>
    request<unknown>(`/api/v1/handover/reports/${reportId}/ack`, {
      method: "POST",
      body: { seconds_to_ack: secondsToAck },
      needsOperator: true,
    }),

  shiftNotes: (hospitalizationId: string) =>
    request<ShiftNote[] | Page<ShiftNote>>(
      `/api/v1/hospitalizations/${hospitalizationId}/shift-notes`,
    ),

  createShiftNote: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<ShiftNote>(`/api/v1/hospitalizations/${hospitalizationId}/shift-notes`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  /* ---- gestão ---- */
  clinic: () => request<ClinicSettings>("/api/v1/clinic"),

  updateClinic: (body: Record<string, unknown>) =>
    request<ClinicSettings>("/api/v1/clinic", { method: "PATCH", body, needsOperator: true }),

  rotateStationKey: () =>
    request<{ station_key: string; station_key_version: number }>(
      "/api/v1/clinic/rotate-station-key",
      { method: "POST", needsOperator: true },
    ),

  /* ---- push: este navegador como aparelho ----
   *
   * A mesma tabela e o mesmo FCM do app da loja: o navegador é só mais um
   * token, com `platform="web"`. Idempotente pelo token, e o conflito MOVE o
   * registro: o mesmo computador passa de mão a cada troca de turno. */
  registerDevice: (token: string, platform: "web" | "ios" | "android") =>
    request<{ id: string; platform: string }>("/api/v1/devices", {
      method: "POST",
      body: { token, platform },
    }),
  unregisterDevice: (token: string) =>
    request<void>(`/api/v1/devices/${encodeURIComponent(token)}`, { method: "DELETE" }),

  /* ---- plataforma: quem vende e dá suporte ----
   *
   * Outra porta, outro token. Nenhuma rota de clínica aceita este token, e
   * ele não passa por PIN: o suporte não executa ato clínico, só administra
   * a assinatura e destrava gente. */
  platformLogin: (email: string, password: string) =>
    request<TokenResponse>("/api/v1/platform/login", { method: "POST", body: { email, password } }),
  platformMe: () => request<PlatformMe>("/api/v1/platform/me"),
  platformClinics: () => request<PlatformClinicRow[]>("/api/v1/platform/clinics"),
  platformClinic: (id: string) => request<PlatformClinic>(`/api/v1/platform/clinics/${id}`),
  platformCreateClinic: (body: Record<string, unknown>) =>
    request<{ clinic: PlatformClinic; admin_email: string; admin_password: string }>(
      "/api/v1/platform/clinics",
      { method: "POST", body },
    ),
  platformUpdateClinic: (id: string, body: Record<string, unknown>) =>
    request<PlatformClinic>(`/api/v1/platform/clinics/${id}`, { method: "PATCH", body }),
  platformResetPassword: (clinicId: string, membershipId: string) =>
    request<{ temporary_password: string }>(
      `/api/v1/platform/clinics/${clinicId}/members/${membershipId}/reset-password`,
      { method: "POST" },
    ),
  platformResetPin: (clinicId: string, membershipId: string) =>
    request<void>(`/api/v1/platform/clinics/${clinicId}/members/${membershipId}/reset-pin`, {
      method: "POST",
    }),

  /* ---- planos: criados, aposentados e migrados pela plataforma ---- */
  platformPlans: () => request<Plan[]>("/api/v1/platform/plans"),
  platformCreatePlan: (body: Record<string, unknown>) =>
    request<Plan>("/api/v1/platform/plans", { method: "POST", body }),
  platformUpdatePlan: (code: string, body: Record<string, unknown>) =>
    request<Plan>(`/api/v1/platform/plans/${code}`, { method: "PATCH", body }),
  platformDeletePlan: (code: string) =>
    request<void>(`/api/v1/platform/plans/${code}`, { method: "DELETE" }),
  platformMigratePlan: (code: string, to: string, retireSource: boolean) =>
    request<{ moved: number; source: Plan; target: Plan }>(
      `/api/v1/platform/plans/${code}/migrate`,
      { method: "POST", body: { to, retire_source: retireSource } },
    ),

  /* ---- aparelhos compartilhados ---- */

  stationDevices: () => request<StationDevice[]>("/api/v1/station-devices"),

  /** Abre a liberação e devolve o código de seis dígitos. Ele sai da API uma
   *  vez só: quem fechar a tela sem anotar precisa abrir outra liberação. */
  openDeviceEnrollment: (name: string) =>
    request<{ device: StationDevice; enrollment_code: string; expires_at: string | null }>(
      "/api/v1/station-devices",
      { method: "POST", body: { name }, needsOperator: true },
    ),

  renameStationDevice: (id: string, name: string) =>
    request<StationDevice>(`/api/v1/station-devices/${id}`, {
      method: "PATCH",
      body: { name },
      needsOperator: true,
    }),

  unlockStationDevice: (id: string) =>
    request<StationDevice>(`/api/v1/station-devices/${id}/unlock`, {
      method: "POST",
      needsOperator: true,
    }),

  revokeStationDevice: (id: string) =>
    request<StationDevice>(`/api/v1/station-devices/${id}/revoke`, {
      method: "POST",
      needsOperator: true,
    }),

  /** Trocar o PROPRIO PIN. Existia só o caminho do administrador definir o de
   *  outra pessoa, e trocar um PIN que alguém viu por cima do ombro dependia
   *  de pedir a terceiros: o incentivo era não trocar. */
  changeMyPin: (currentPin: string | null, newPin: string) =>
    request<void>("/api/v1/auth/me/pin", {
      method: "PUT",
      body: { current_pin: currentPin, new_pin: newPin },
    }),

  memberships: () => request<MembershipRow[] | Page<MembershipRow>>("/api/v1/memberships"),

  /** Roster: quem é quem, legível por qualquer membro (a escala precisa disso). */
  membershipRoster: () => request<MembershipRoster[]>("/api/v1/memberships/roster"),

  createMembership: (body: Record<string, unknown>) =>
    request<MembershipRow>("/api/v1/memberships", { method: "POST", body, needsOperator: true }),

  updateMembership: (id: string, body: Record<string, unknown>) =>
    request<MembershipRow>(`/api/v1/memberships/${id}`, {
      method: "PATCH",
      body,
      needsOperator: true,
    }),

  setMembershipPin: (id: string, pin: string) =>
    request<void>(`/api/v1/memberships/${id}/pin`, {
      method: "POST",
      body: { pin },
      needsOperator: true,
    }),

  /* ---- tutor ---- */
  ownerContacts: (hospitalizationId: string) =>
    request<OwnerContact[] | Page<OwnerContact>>(
      `/api/v1/hospitalizations/${hospitalizationId}/owner-contacts`,
    ),

  createOwnerContact: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<OwnerContact>(`/api/v1/hospitalizations/${hospitalizationId}/owner-contacts`, {
      method: "POST",
      body,
      needsOperator: true,
    }),

  sendWhatsAppBulletin: (hospitalizationId: string, body: Record<string, unknown>) =>
    request<OwnerContact>(
      `/api/v1/hospitalizations/${hospitalizationId}/owner-contacts/whatsapp`,
      { method: "POST", body, needsOperator: true },
    ),

  /* ---- auditoria ---- */
  audit: (query: { entity_type?: string; entity_id?: string; cursor?: string; limit?: number } = {}) =>
    request<Page<AuditEntry>>("/api/v1/audit", { query }),
};

/** Muitas rotas devolvem lista pura ou Page; normaliza para array. */
export function asList<T>(value: T[] | Page<T> | undefined | null): T[] {
  if (!value) return [];
  return Array.isArray(value) ? value : value.items;
}
