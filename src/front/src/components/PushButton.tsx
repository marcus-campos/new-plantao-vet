import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { enablePush, pushState, pushSupported, type PushState } from "../push";
import { useSession } from "../hooks/useSession";

/** Ligar os alertas neste navegador.
 *
 *  Só na sessão pessoal: a estação é um tablet compartilhado, e o alerta é
 *  da pessoa de plantão, não do aparelho do corredor. Só existe quando o build
 *  tem Firebase e o navegador suporta push: botão que não consegue receber é
 *  pior que nenhum. E a permissão só é pedida no clique, nunca ao abrir. */
export function PushButton() {
  const { t } = useTranslation();
  const { session } = useSession();
  const [supported, setSupported] = useState(false);
  const [state, setState] = useState<PushState>(() => pushState());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    void pushSupported().then((ok) => {
      if (alive) setSupported(ok);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Some depois de ativado: o botão existe para conseguir a permissão, e uma
  // vez conseguida ele não tem mais trabalho a fazer na barra. Quem quiser
  // desligar usa as configurações do site no navegador, que é onde a permissão
  // de notificação realmente vive.
  if (session?.kind !== "personal" || !supported || state === "unsupported" || state === "on") {
    return null;
  }

  async function ligar() {
    setBusy(true);
    try {
      setState(await enablePush());
    } finally {
      setBusy(false);
    }
  }

  if (state === "blocked") {
    return (
      <span className="nav-link push-blocked" title={t("push.blockedHint")}>
        {t("push.blocked")}
      </span>
    );
  }

  return (
    <button
      type="button"
      className="nav-link"
      disabled={busy}
      onClick={() => void ligar()}
      title={t("push.offHint")}
    >
      {t("push.off")}
    </button>
  );
}
