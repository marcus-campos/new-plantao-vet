import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./i18n";
import "./index.css";
import "./layout.css";
import { resumePush } from "./push";
import { SessionProvider } from "./hooks/useSession";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);

// Quem já ligou os alertas neste navegador tem o token renovado em silêncio:
// token do FCM rotaciona, e sem isto o aviso para de chegar sem ninguém
// perceber. Nunca pede permissão daqui.
void resumePush();
