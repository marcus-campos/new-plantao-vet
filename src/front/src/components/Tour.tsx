import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { useSession } from "../hooks/useSession";
import "../styles/tour.css";

/** Um passo do tour. `alvo` é o seletor do elemento a destacar; sem ele, o
 *  cartão fica centrado — é o caso da abertura e do encerramento. */
type Passo = { chave: string; alvo?: string };

/** O roteiro muda com o papel porque o trabalho muda com o papel: quem
 *  administra precisa achar a equipe e os boxes antes de qualquer coisa, quem
 *  prescreve precisa achar a ficha, e quem executa precisa achar o plantão.
 *  Um tour único mostraria a três pessoas a tela de uma delas. */
const ROTEIRO: Record<string, Passo[]> = {
  admin: [
    { chave: "welcomeAdmin" },
    { chave: "management", alvo: '[data-tour="/gestao"]' },
    { chave: "inpatients", alvo: '[data-tour="/internados"]' },
    { chave: "admitAdmin" },
  ],
  vet: [
    { chave: "welcomeVet" },
    { chave: "shift", alvo: '[data-tour="/plantao"]' },
    { chave: "inpatients", alvo: '[data-tour="/internados"]' },
    { chave: "handover", alvo: '[data-tour="/passagem"]' },
  ],
  tech: [
    { chave: "welcomeTech" },
    { chave: "shift", alvo: '[data-tour="/plantao"]' },
    { chave: "inpatients", alvo: '[data-tour="/internados"]' },
  ],
};

/** Onde o buraco do holofote fica, em coordenadas da janela. */
type Furo = { top: number; left: number; width: number; height: number } | null;

/** O tour de boas-vindas do primeiro acesso.
 *
 *  Aparece uma vez por pessoa por clínica, guiado pelo que a API respondeu em
 *  `/auth/me`. Não é um vídeo nem um modal de texto: destaca o item real da
 *  navegação e diz para que ele serve, para que a segunda visita já seja
 *  reconhecimento em vez de descoberta.
 *
 *  Marca como visto ao terminar E ao dispensar. Quem fechou não quer ver de
 *  novo, e insistir seria transformar boas-vindas em obstáculo. */
export function Tour() {
  const { t } = useTranslation();
  const { session, me } = useSession();
  const [passo, setPasso] = useState(0);
  const [furo, setFuro] = useState<Furo>(null);
  const [fechado, setFechado] = useState(false);
  const cartao = useRef<HTMLDivElement | null>(null);

  const papel = me?.role ?? "";
  const roteiro = ROTEIRO[papel] ?? [];
  const ativo =
    session?.kind === "personal" && me != null && !me.tour_done && roteiro.length > 0 && !fechado;
  const atual = roteiro[passo];

  const encerrar = useCallback(() => {
    setFechado(true);
    // Otimista de propósito: a pessoa já viu o tour, e uma falha de rede não
    // pode fazê-lo voltar na próxima tela. A rota é idempotente, então uma
    // tentativa perdida se resolve no próximo carregamento.
    void api.finishTour().catch(() => undefined);
  }, []);

  // A posição do furo vem do elemento real, e é recalculada quando a janela
  // muda: o menu se reorganiza em telas estreitas, e um holofote apontando
  // para o lugar errado é pior que nenhum.
  useLayoutEffect(() => {
    if (!ativo || !atual) return;
    const medir = () => {
      if (!atual.alvo) return setFuro(null);
      const el = document.querySelector(atual.alvo);
      if (!el) return setFuro(null);
      const r = el.getBoundingClientRect();
      setFuro({ top: r.top - 6, left: r.left - 8, width: r.width + 16, height: r.height + 12 });
    };
    medir();
    window.addEventListener("resize", medir);
    window.addEventListener("scroll", medir, true);
    return () => {
      window.removeEventListener("resize", medir);
      window.removeEventListener("scroll", medir, true);
    };
  }, [ativo, atual]);

  // Teclado: Esc dispensa, setas e Enter avançam. Um passo a passo que só
  // responde ao mouse deixa de fora quem opera no teclado.
  useEffect(() => {
    if (!ativo) return;
    cartao.current?.focus();
    const tecla = (e: KeyboardEvent) => {
      if (e.key === "Escape") encerrar();
      if (e.key === "ArrowRight" || e.key === "Enter") {
        if (passo + 1 >= roteiro.length) encerrar();
        else setPasso((p) => p + 1);
      }
      if (e.key === "ArrowLeft" && passo > 0) setPasso((p) => p - 1);
    };
    window.addEventListener("keydown", tecla);
    return () => window.removeEventListener("keydown", tecla);
  }, [ativo, passo, roteiro.length, encerrar]);

  if (!ativo || !atual) return null;

  const ultimo = passo + 1 >= roteiro.length;
  // O cartão fica abaixo do furo quando há um; sem furo, no centro da tela.
  const estilo = furo
    ? { top: furo.top + furo.height + 14, left: Math.max(12, Math.min(furo.left, window.innerWidth - 356)) }
    : undefined;

  return (
    <div className="tour" role="dialog" aria-modal="true" aria-labelledby="tour-titulo">
      {/* O holofote é uma sombra gigante para fora de um retângulo: recorta o
          alvo sem precisar clonar o elemento nem mexer no layout da página. */}
      <div className="tour-veu" onClick={encerrar}>
        {furo ? (
          <span
            className="tour-furo"
            style={{ top: furo.top, left: furo.left, width: furo.width, height: furo.height }}
          />
        ) : null}
      </div>

      <div
        className={`tour-cartao ${furo ? "" : "tour-cartao-centro"}`}
        style={estilo}
        ref={cartao}
        tabIndex={-1}
      >
        <h2 id="tour-titulo">{t(`tour.${atual.chave}.title`)}</h2>
        <p>{t(`tour.${atual.chave}.body`)}</p>

        <div className="tour-pe">
          <span className="tour-pontos" aria-label={t("tour.progress", { n: passo + 1, total: roteiro.length })}>
            {roteiro.map((p, i) => (
              <i key={p.chave} className={i === passo ? "is-atual" : ""} />
            ))}
          </span>
          <div className="tour-acoes">
            <button type="button" className="tour-pular" onClick={encerrar}>
              {ultimo ? t("tour.close") : t("tour.skip")}
            </button>
            {ultimo ? null : (
              <button type="button" className="tour-proximo" onClick={() => setPasso((p) => p + 1)}>
                {t("tour.next")}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
