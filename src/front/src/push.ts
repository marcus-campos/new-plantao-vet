import { initializeApp, type FirebaseApp } from "firebase/app";
import { getMessaging, getToken, isSupported, onMessage, type Messaging } from "firebase/messaging";

import { api } from "./api/client";

/** Push no NAVEGADOR, pelo mesmo caminho do app.
 *
 *  O backend já manda alerta por FCM para o celular; o navegador é só mais um
 *  token na mesma tabela de aparelhos, com `platform="web"`. Nada de segundo
 *  provedor: o mesmo orçamento de alertas, a mesma varredura de dose crítica,
 *  o mesmo "avisar o veterinário" chegam no Chrome do técnico enquanto o app
 *  da loja não sai.
 *
 *  A configuração do Firebase vem do build (`VITE_FIREBASE_CONFIG`, um JSON, e
 *  `VITE_FIREBASE_VAPID_KEY`). Sem ela, nada aqui aparece na interface: um
 *  botão "receber alertas" que não consegue receber é pior que nenhum.
 *
 *  A permissão NUNCA é pedida sozinha ao abrir a página: navegador penaliza
 *  site que pede na cara, e a pessoa nega por reflexo. Pede-se no clique de
 *  um botão que diz o que vai acontecer. */

const STORAGE_KEY = "plantaovet.push";

interface FirebaseConfig {
  apiKey: string;
  projectId: string;
  messagingSenderId: string;
  appId: string;
  [key: string]: string;
}

function config(): FirebaseConfig | null {
  const raw = import.meta.env.VITE_FIREBASE_CONFIG as string | undefined;
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as FirebaseConfig;
    return parsed.apiKey && parsed.projectId && parsed.messagingSenderId && parsed.appId
      ? parsed
      : null;
  } catch {
    return null;
  }
}

const VAPID_KEY = (import.meta.env.VITE_FIREBASE_VAPID_KEY as string | undefined) ?? "";

let app: FirebaseApp | null = null;
let messaging: Messaging | null = null;
let registration: ServiceWorkerRegistration | null = null;

/** O navegador e o build permitem push? Decide se o botão existe. */
export async function pushSupported(): Promise<boolean> {
  if (!config() || !VAPID_KEY) return false;
  if (typeof window === "undefined" || !("Notification" in window)) return false;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) return false;
  return isSupported();
}

export type PushState = "unsupported" | "off" | "on" | "blocked";

export function pushState(): PushState {
  if (!config() || !VAPID_KEY || typeof Notification === "undefined") return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  let saved: string | null = null;
  try {
    saved = localStorage.getItem(STORAGE_KEY);
  } catch {
    /* sem storage: trata como desligado */
  }
  return saved === "on" && Notification.permission === "granted" ? "on" : "off";
}

/** Registra o service worker do Firebase UMA vez.
 *
 *  A configuração vai na query string: o service worker não lê variáveis do
 *  Vite, e gerar o arquivo no build seria um plugin a mais para carregar
 *  quatro strings. */
async function ensureRegistration(): Promise<ServiceWorkerRegistration> {
  if (registration) return registration;
  const cfg = config();
  const url = `/firebase-messaging-sw.js?config=${encodeURIComponent(JSON.stringify(cfg))}`;
  registration = await navigator.serviceWorker.register(url);
  return registration;
}

function ensureMessaging(): Messaging {
  if (messaging) return messaging;
  const cfg = config();
  if (!cfg) throw new Error("push: sem configuração do Firebase");
  app = app ?? initializeApp(cfg);
  messaging = getMessaging(app);
  // Com a aba aberta o FCM entrega aqui, não no service worker. Mostra-se a
  // mesma notificação, pelo mesmo caminho, para o aviso não depender de a
  // pessoa estar olhando esta aba.
  onMessage(messaging, (payload) => {
    const title = payload.notification?.title ?? "PlantãoVet";
    const body = payload.notification?.body ?? "";
    const link = (payload.fcmOptions as { link?: string } | undefined)?.link ?? "/plantao";
    void registration?.showNotification(title, {
      body,
      icon: "/icons/icon-192.png",
      badge: "/icons/icon-192.png",
      tag: payload.data?.event ?? "plantaovet",
      data: { url: link },
    });
  });
  return messaging;
}

/** Liga os alertas neste navegador: pede permissão, pega o token e registra.
 *
 *  Só para sessão PESSOAL. A estação é um tablet compartilhado, e o alerta é
 *  da pessoa de plantão, não do aparelho do corredor. */
export async function enablePush(): Promise<PushState> {
  if (!(await pushSupported())) return "unsupported";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "blocked" : "off";
  const reg = await ensureRegistration();
  const token = await getToken(ensureMessaging(), {
    vapidKey: VAPID_KEY,
    serviceWorkerRegistration: reg,
  });
  await api.registerDevice(token, "web");
  try {
    localStorage.setItem(STORAGE_KEY, "on");
  } catch {
    /* sem storage: vale só nesta aba */
  }
  return "on";
}

/** Desliga: tira o token do servidor e esquece a escolha. A permissão do
 *  navegador continua concedida; é o site que para de usá-la. */
export async function disablePush(): Promise<void> {
  try {
    const reg = await ensureRegistration();
    const token = await getToken(ensureMessaging(), {
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: reg,
    });
    await api.unregisterDevice(token);
  } catch {
    /* token já morto ou sem rede: o servidor aposenta no próximo envio */
  }
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* nada a esquecer */
  }
}

/** Ao abrir: se a pessoa já ligou os alertas aqui, renova o token em silêncio.
 *  Token do FCM rotaciona; sem isto o alerta para de chegar sem aviso. */
export async function resumePush(): Promise<void> {
  if (pushState() !== "on") return;
  try {
    const reg = await ensureRegistration();
    const token = await getToken(ensureMessaging(), {
      vapidKey: VAPID_KEY,
      serviceWorkerRegistration: reg,
    });
    await api.registerDevice(token, "web");
  } catch {
    /* sem rede ou sem token: tenta de novo na próxima abertura */
  }
}
