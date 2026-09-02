/* Service worker do push no navegador.
 *
 * Recebe a configuração do Firebase pela query string do registro: um service
 * worker não lê variáveis do Vite, e gerar este arquivo no build seria um
 * plugin a mais para carregar quatro strings.
 *
 * Não há `onBackgroundMessage`: o FCM mostra a notificação sozinho a partir do
 * bloco `notification` + `webpush` que o backend manda, e trata o clique com
 * `fcm_options.link` (abre a ficha do paciente). Menos código aqui é menos
 * código que só roda com a aba fechada, onde ninguém vê erro. */
importScripts("https://www.gstatic.com/firebasejs/11.10.0/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/11.10.0/firebase-messaging-compat.js");

const params = new URL(self.location.href).searchParams;
const raw = params.get("config");
if (raw) {
  try {
    firebase.initializeApp(JSON.parse(raw));
    firebase.messaging();
  } catch (err) {
    console.warn("push: configuração inválida", err);
  }
}

/* Notificação mostrada pela própria aba (mensagem em primeiro plano) carrega
   a URL em `data.url`; o clique abre ou foca a aba certa. */
self.addEventListener("notificationclick", (event) => {
  const url = (event.notification && event.notification.data && event.notification.data.url) || "/plantao";
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if ("focus" in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      return self.clients.openWindow(url);
    }),
  );
});

/* Instalável: o navegador quer um service worker com `fetch`. Passa direto:
   offline degradado está fora do escopo (spec), e um cache aqui viraria uma
   segunda fonte de verdade para a ficha do paciente. */
self.addEventListener("fetch", () => {});
