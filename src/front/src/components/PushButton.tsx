import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { disablePush, enablePush, pushState, pushSupported, type PushState } from "../push";
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

  if (session?.kind !== "personal" || !supported || state === "unsupported") return null;

  async function toggle() {
    setBusy(true);
    try {
      if (state === "on") {
        await disablePush();
        setState("off");
      } else {
        setState(await enablePush());
      }
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
      className={state === "on" ? "nav-link push-on" : "nav-link"}
      disabled={busy}
      onClick={() => void toggle()}
      title={state === "on" ? t("push.onHint") : t("push.offHint")}
    >
      {state === "on" ? t("push.on") : t("push.off")}
    </button>
  );
}
