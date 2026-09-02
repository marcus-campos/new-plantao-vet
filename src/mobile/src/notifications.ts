import * as Device from "expo-device";
import * as Notifications from "expo-notifications";
import { Platform } from "react-native";

import { api } from "./api/client";

/** Alerta no bolso: tarefa crítica atrasada precisa cutucar quem está entre os boxes.
 *
 *  Disciplina anti-ruído (pesquisa §4: 74–99% dos alarmes de UTI não exigem ação):
 *  notificação ativa SÓ para tarefa crítica fora da janela. O resto é escalonamento
 *  visual no painel e na fila, nunca um alerta a mais. */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: true,
  }),
});

export async function registerForPushNotifications(): Promise<string | null> {
  if (!Device.isDevice) return null; // simulador não recebe push

  if (Platform.OS === "android") {
    await Notifications.setNotificationChannelAsync("critical", {
      name: "Tarefas críticas atrasadas",
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 250, 250, 250],
    });
  }

  const existing = await Notifications.getPermissionsAsync();
  let status = existing.status;
  if (status !== "granted") {
    status = (await Notifications.requestPermissionsAsync()).status;
  }
  if (status !== "granted") return null;

  // Token NATIVO (FCM no Android, APNs no iOS), não o do Expo.
  //
  // `getExpoPushTokenAsync` devolve `ExponentPushToken[…]`, que só o serviço do
  // Expo entende. O FCM v1 recusa com 400 em todo envio. O servidor aceita e
  // guarda os dois, mas marca o do Expo como não-entregável e o pula: um token
  // que nunca entrega é pior que nenhum, porque some do radar como se estivesse
  // funcionando.
  const token = await Notifications.getDevicePushTokenAsync();
  return typeof token.data === "string" ? token.data : null;
}

/** Registra o aparelho no servidor.
 *
 *  Sem isto o app pedia permissão de notificação à pessoa e jogava o token
 *  fora: não havia rota para recebê-lo nem tabela para guardá-lo, então o
 *  "alerta no bolso" não existia em forma nenhuma. */
export async function registerDevice(): Promise<string | null> {
  const token = await registerForPushNotifications();
  if (!token) return null;
  try {
    await api.registerDevice(token, Platform.OS);
    return token;
  } catch {
    // Sem push a operação continua inteira: a fila e o painel seguem sendo a
    // fonte. Não vale interromper quem está entre os boxes por causa disso.
    return null;
  }
}

/** Logout e desinstalação: o aparelho para de receber o plantão de quem saiu. */
export async function unregisterDevice(token: string): Promise<void> {
  try {
    await api.unregisterDevice(token);
  } catch {
    // O servidor também desativa o token quando o FCM o recusa.
  }
}

/** O lembrete LOCAL foi removido.
 *
 *  Era o paliativo de enquanto o push do servidor não existia, e nem ele nem
 *  o cancelamento eram chamados por tela nenhuma. Com o push real, manter os
 *  dois faria a mesma dose crítica alertar duas vezes, que é exatamente o
 *  ruído que o orçamento de alertas existe para evitar (só 5–13% dos alarmes
 *  de UTI são acionáveis).
 */
