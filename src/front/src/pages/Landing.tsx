import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { SignupForm } from "../components/SignupForm";
import "../styles/landing.css";

const WHATSAPP_URL =
  "https://wa.me/5561983031823?text=Ol%C3%A1%21%20Quero%20saber%20mais%20sobre%20o%20Plant%C3%A3oVet";

/** Respeita a preferência do sistema por menos movimento.
 *
 *  A landing usa animação para EXPLICAR (uma prescrição virando três horários,
 *  uma dose vencendo a janela). Para quem pediu menos movimento, o estado final
 *  aparece de uma vez: a informação é a mesma, sem a transição. */
function useReducedMotion() {
  // Lido na inicialização, não num efeito: assim a primeira pintura já respeita
  // a preferência, em vez de animar por um frame antes de descobrir.
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const ouvir = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", ouvir);
    return () => mq.removeEventListener("change", ouvir);
  }, []);
  return reduced;
}

/** Dispara uma vez quando o elemento entra na tela.
 *
 *  As demonstrações só animam quando alguém está olhando — animar fora da tela
 *  gasta bateria e faz a pessoa perder justamente a parte que explica. */
function useOnScreen<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [visivel, setVisivel] = useState(false);
  useEffect(() => {
    const alvo = ref.current;
    if (!alvo || visivel) return;
    const obs = new IntersectionObserver(
      ([entrada]) => {
        if (entrada.isIntersecting) setVisivel(true);
      },
      { threshold: 0.35 },
    );
    obs.observe(alvo);
    // Rede de segurança: se o observer não disparar em 2 segundos — porque o
    // navegador não o implementa, porque a seção nunca cruza o limiar, ou
    // porque a página foi renderizada fora de uma viewport real — o conteúdo
    // aparece assim mesmo. Perder a animação é aceitável; perder o texto não.
    const rede = window.setTimeout(() => setVisivel(true), 2000);
    return () => {
      obs.disconnect();
      window.clearTimeout(rede);
    };
  }, [visivel]);
  return [ref, visivel] as const;
}

/** Revela um elemento quando ele entra na tela. Usado pelas seções inteiras.
 *
 *  Devolve `is-in` só depois que o observer viu o elemento; o CSS esconde
 *  apenas quando a página está `lp-armado`, então sem JS nada some. */
function useRevelar<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [dentro, setDentro] = useState(false);
  useEffect(() => {
    const alvo = ref.current;
    if (!alvo || dentro) return;
    const obs = new IntersectionObserver(
      ([e]) => {
        if (e.isIntersecting) setDentro(true);
      },
      { threshold: 0.16, rootMargin: "0px 0px -8% 0px" },
    );
    obs.observe(alvo);
    const rede = window.setTimeout(() => setDentro(true), 2600);
    return () => {
      obs.disconnect();
      window.clearTimeout(rede);
    };
  }, [dentro]);
  return { ref, cls: dentro ? "lp-rev is-in" : "lp-rev" };
}

function Check({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M20 6L9 17l-5-5" />
    </svg>
  );
}

function Alert({ size = 16 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 8v5M12 17h.01" />
      <circle cx="12" cy="12" r="9" />
    </svg>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   A ficha viva do hero.

   É o produto, não uma ilustração: a mesma grade hora × tarefa da ficha de
   internação, com os horários que o aprazamento realmente produz (as âncoras
   padrão da clínica: q8h → 10:00, 18:00, 02:00). O relógio anda, e a dose das
   22h cruza a janela de tolerância na frente de quem está lendo. É a tese da
   página em movimento, antes de qualquer parágrafo.
   ───────────────────────────────────────────────────────────────────────── */

type LinhaFicha = {
  hora: string;
  nome: string;
  detalhe: string;
  critica?: boolean;
  estado: "feita" | "programada" | "vencendo";
  autor?: string;
};

/** O relógio do hero: onde ele começa e onde para. A janela normal é de 60
 *  minutos, então a dose vence no caminho entre os dois. */
const ANTES = 47;
const DEPOIS = 66;

const FICHA: LinhaFicha[] = [
  { hora: "18:00", nome: "Dipirona", detalhe: "25 mg/kg · IV", estado: "feita", autor: "MC" },
  { hora: "20:00", nome: "Checagem da bomba", detalhe: "Ringer 60 ml/h", estado: "feita", autor: "JR" },
  { hora: "22:00", nome: "Dipirona", detalhe: "25 mg/kg · IV", estado: "vencendo" },
  { hora: "22:00", nome: "Pressão arterial", detalhe: "não invasiva", critica: true, estado: "programada" },
  { hora: "02:00", nome: "Dipirona", detalhe: "25 mg/kg · IV", estado: "programada" },
];

function FichaViva() {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  // Começa em 47 minutos, não em zero: a dose já chega perto de vencer e cruza
  // a janela em pouco mais de um segundo. Zerar o relógio custava quatro
  // segundos de ficha tranquila — e quem rolasse a página nesse intervalo via
  // exatamente o contrário do que a headline promete.
  const [minutos, setMinutos] = useState(reduced ? DEPOIS : ANTES);

  useEffect(() => {
    if (reduced) return;
    // 47 → 66 minutos depois das 22h. A dose atravessa a janela normal (60 min)
    // no meio do caminho: o vermelho não é decoração, é a regra acontecendo.
    const inicio = performance.now();
    let frame = 0;
    const passo = (agora: number) => {
      const decorrido = Math.min((agora - inicio) / 1800, 1);
      setMinutos(Math.round(ANTES + decorrido * (DEPOIS - ANTES)));
      if (decorrido < 1) frame = requestAnimationFrame(passo);
    };
    frame = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(frame);
  }, [reduced]);

  const atrasada = minutos > 60;
  const relogio = useMemo(() => {
    const total = 22 * 60 + minutos;
    const h = Math.floor(total / 60) % 24;
    const m = total % 60;
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  }, [minutos]);

  return (
    <figure className={`lp-ficha ${atrasada ? "is-late" : ""}`} aria-label={t("lp.sheet.aria")}>
      <div className="lp-ficha-top">
        <div className="lp-ficha-paciente">
          <strong>Thor</strong>
          <span>{t("lp.sheet.patient")}</span>
        </div>
        <div className="lp-ficha-relogio" aria-live="off">
          <span className="lp-pulso" aria-hidden="true" />
          {relogio}
        </div>
      </div>

      <ul className="lp-ficha-linhas">
        {FICHA.map((linha, i) => {
          const venceu = linha.estado === "vencendo" && atrasada;
          const estado = venceu ? "atrasada" : linha.estado;
          return (
            <li key={i} className={`lp-linha lp-linha-${estado}`}>
              <span className="lp-linha-hora tabular">{linha.hora}</span>
              <span className="lp-linha-nome">
                {linha.nome}
                {linha.critica ? <em className="lp-critica">{t("lp.sheet.critical")}</em> : null}
                <small>{linha.detalhe}</small>
              </span>
              <span className="lp-linha-estado">
                {estado === "feita" ? (
                  <>
                    <Check size={15} />
                    <b className="tabular">{linha.autor}</b>
                  </>
                ) : estado === "atrasada" ? (
                  <>
                    <Alert size={15} />
                    <b>{t("lp.sheet.late", { min: minutos - 60 })}</b>
                  </>
                ) : (
                  <b className="lp-programada">{t("lp.sheet.scheduled")}</b>
                )}
              </span>
            </li>
          );
        })}
      </ul>

      <figcaption className={`lp-ficha-pe ${atrasada ? "is-late" : ""}`}>
        {atrasada ? t("lp.sheet.footLate") : t("lp.sheet.foot")}
      </figcaption>
    </figure>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   Prescrição → aprazamento → execução.
   Uma linha de receita virando três horários, e os três horários virando
   estados. É a mecânica central do produto, e explicar isso com texto custa
   três parágrafos que ninguém lê.
   ───────────────────────────────────────────────────────────────────────── */
function FluxoPrescricao() {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [ref, visivel] = useOnScreen<HTMLDivElement>();
  // Arma a entrada só depois da montagem: até lá o CSS deixa tudo visível, e
  // um observer que não dispare nunca esconde o conteúdo.
  const [armado, setArmado] = useState(false);
  useEffect(() => {
    if (!reduced) setArmado(true);
  }, [reduced]);
  const rodar = visivel || reduced;

  return (
    <div className={`lp-fluxo ${armado ? "is-armed" : ""}`} ref={ref}>
      <div className={`lp-fluxo-passo ${rodar ? "is-in" : ""}`}>
        <h3>{t("lp.flow.a.title")}</h3>
        <div className="lp-receita">
          <strong>Dipirona</strong>
          <span>25 mg/kg</span>
          <span>IV</span>
          <span className="lp-freq">q8h</span>
        </div>
        <p>{t("lp.flow.a.body")}</p>
      </div>

      <div className={`lp-fluxo-passo lp-delay-1 ${rodar ? "is-in" : ""}`}>
        <h3>{t("lp.flow.b.title")}</h3>
        <div className="lp-horarios">
          {["10:00", "18:00", "02:00"].map((h) => (
            <span key={h} className="tabular">
              {h}
            </span>
          ))}
        </div>
        <p>{t("lp.flow.b.body")}</p>
      </div>

      <div className={`lp-fluxo-passo lp-delay-2 ${rodar ? "is-in" : ""}`}>
        <h3>{t("lp.flow.c.title")}</h3>
        <div className="lp-execucoes">
          <span className="lp-ex-feita">
            <Check size={14} /> <b className="tabular">10:03</b>
          </span>
          <span className="lp-ex-atrasada">
            <Alert size={14} /> <b className="tabular">18:00</b>
          </span>
          <span className="lp-ex-prog">
            <b className="tabular">02:00</b>
          </span>
        </div>
        <p>{t("lp.flow.c.body")}</p>
      </div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────────────────
   A passagem de plantão: o que atravessa a troca de turno.
   ───────────────────────────────────────────────────────────────────────── */
function FluxoPassagem() {
  const { t } = useTranslation();
  const itens = ["done", "notDone", "pending", "changes"] as const;
  return (
    <div className="lp-passagem">
      <div className="lp-turno lp-turno-sai">
        <span className="lp-turno-rot">{t("lp.handover.out")}</span>
        <strong>07h — 19h</strong>
      </div>

      <ul className="lp-passagem-itens">
        {itens.map((k) => (
          <li key={k}>
            <span className={`lp-bolinha lp-bolinha-${k}`} aria-hidden="true" />
            {t(`lp.handover.item.${k}`)}
          </li>
        ))}
      </ul>

      <div className="lp-aceite">
        <Check size={16} />
        {t("lp.handover.ack")}
      </div>

      <div className="lp-turno lp-turno-entra">
        <span className="lp-turno-rot">{t("lp.handover.in")}</span>
        <strong>19h — 07h</strong>
      </div>
    </div>
  );
}

/* ───────────────────────────────────────────────────────────────────────── */

const SUPERFICIES = ["web", "board", "app", "station"] as const;
const FAQS = ["erp", "record", "mobile", "install", "beds", "over", "after", "export", "who", "team"] as const;
const PASSOS = ["a", "b", "c"] as const;

export function Landing() {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  // `lp-armado` é o que autoriza o CSS a esconder as seções antes de revelá-las.
  // Só entra depois da montagem, e nunca para quem pediu menos movimento: assim
  // a página sem JS, ou com o observer falhando, continua legível por completo.
  const [armado, setArmado] = useState(false);
  const [colado, setColado] = useState(false);

  useEffect(() => {
    if (!reduced) setArmado(true);
  }, [reduced]);

  useEffect(() => {
    const aoRolar = () => setColado(window.scrollY > 8);
    aoRolar();
    window.addEventListener("scroll", aoRolar, { passive: true });
    return () => window.removeEventListener("scroll", aoRolar);
  }, []);

  const cena = useRevelar<HTMLElement>();
  const fluxo = useRevelar<HTMLElement>();
  const atraso = useRevelar<HTMLElement>();
  const passagem = useRevelar<HTMLElement>();
  const superficies = useRevelar<HTMLElement>();
  const estacao = useRevelar<HTMLElement>();
  const trace = useRevelar<HTMLElement>();
  const comecar = useRevelar<HTMLElement>();
  const preco = useRevelar<HTMLElement>();
  const teste = useRevelar<HTMLElement>();
  const faq = useRevelar<HTMLElement>();
  const criar = useRevelar<HTMLElement>();

  return (
    <div className={`lp ${armado ? "lp-armado" : ""}`}>
      <header className={`lp-nav ${colado ? "is-stuck" : ""}`}>
        <a className="lp-marca" href="#topo">
          Plantão<span>Vet</span>
        </a>
        <nav>
          <a href="#como">{t("lp.nav.how")}</a>
          <a href="#preco">{t("lp.nav.pricing")}</a>
          <Link to="/entrar" className="lp-nav-entrar">
            {t("lp.nav.signIn")}
          </Link>
        </nav>
      </header>

      <main id="topo">
        {/* HERO ─ a tese e o produto na mesma tela. */}
        <section className="lp-hero">
          <div className="lp-hero-texto">
            <h1 className="lp-sobe lp-sobe-1">{t("lp.hero.title")}</h1>
            <p className="lp-hero-sub lp-sobe lp-sobe-2">{t("lp.hero.sub")}</p>
            <div className="lp-hero-acoes lp-sobe lp-sobe-3">
              <a href="#criar" className="lp-btn lp-btn-primario">
                {t("lp.cta.primary")}
              </a>
              <a href="#fluxo" className="lp-btn lp-btn-fantasma">
                {t("lp.cta.secondary")}
              </a>
            </div>
            <p className="lp-hero-nota lp-sobe lp-sobe-4">{t("lp.hero.note")}</p>
          </div>
          <div className="lp-hero-produto lp-sobe lp-sobe-5">
            <FichaViva />
          </div>
        </section>

        {/* A CENA ─ o reconhecimento. Uma frase por linha, no ritmo de quem
            conta o que aconteceu no plantão de ontem. */}
        <section className={`lp-cena ${cena.cls}`} ref={cena.ref}>
          <p className="lp-cena-linha">{t("lp.scene.a")}</p>
          <p className="lp-cena-linha">{t("lp.scene.b")}</p>
          <p className="lp-cena-linha">{t("lp.scene.c")}</p>
          <p className="lp-cena-virada">{t("lp.scene.turn")}</p>
        </section>

        {/* PRESCRIÇÃO → EXECUÇÃO */}
        <section className={`lp-secao ${fluxo.cls}`} id="fluxo" ref={fluxo.ref}>
          <h2>{t("lp.flow.title")}</h2>
          <p className="lp-secao-sub">{t("lp.flow.sub")}</p>
          <FluxoPrescricao />
        </section>

        {/* O ATRASO */}
        <section className={`lp-secao ${atraso.cls}`} ref={atraso.ref}>
          <h2>{t("lp.late.title")}</h2>
          <p className="lp-secao-sub">{t("lp.late.sub")}</p>
          <div className="lp-janelas">
            {[
              { k: "critical", min: 30 },
              { k: "normal", min: 60 },
              { k: "daily", min: 120 },
            ].map(({ k, min }) => (
              <div key={k} className="lp-janela">
                <span className="lp-janela-min tabular">{min}</span>
                <span className="lp-janela-un">min</span>
                <span className="lp-janela-nome">{t(`lp.late.${k}`)}</span>
              </div>
            ))}
          </div>
          <p className="lp-nota">{t("lp.late.note")}</p>
        </section>

        {/* PASSAGEM DE PLANTÃO */}
        <section className={`lp-secao ${passagem.cls}`} id="passagem" ref={passagem.ref}>
          <h2>{t("lp.handover.title")}</h2>
          <p className="lp-secao-sub">{t("lp.handover.sub")}</p>
          <FluxoPassagem />
          <p className="lp-frase">{t("lp.handover.line")}</p>
        </section>

        {/* SUPERFÍCIES */}
        <section className={`lp-secao ${superficies.cls}`} id="como" ref={superficies.ref}>
          <h2>{t("lp.surfaces.title")}</h2>
          <p className="lp-frase lp-frase-alta">{t("lp.surfaces.line")}</p>
          <div className="lp-superficies">
            {SUPERFICIES.map((s) => (
              <article key={s} className="lp-superficie">
                <h3>{t(`lp.surfaces.${s}.name`)}</h3>
                <p>{t(`lp.surfaces.${s}.body`)}</p>
              </article>
            ))}
          </div>
        </section>

        {/* ESTAÇÃO */}
        <section className={`lp-estacao ${estacao.cls}`} ref={estacao.ref}>
          <h2>{t("lp.station.title")}</h2>
          <p>{t("lp.station.body")}</p>
          <div className="lp-pin">
            <span className="lp-pin-caixa" aria-hidden="true">
              <i />
              <i />
              <i />
              <i />
            </span>
            <span className="lp-pin-nome">{t("lp.station.who")}</span>
          </div>
        </section>

        {/* RASTREABILIDADE */}
        <section className={`lp-secao ${trace.cls}`} ref={trace.ref}>
          <h2>{t("lp.trace.title")}</h2>
          <p className="lp-secao-sub">{t("lp.trace.sub")}</p>
          <dl className="lp-trace">
            {["who", "when", "what", "keep"].map((k) => (
              <div key={k}>
                <dt>{t(`lp.trace.${k}.t`)}</dt>
                <dd>{t(`lp.trace.${k}.d`)}</dd>
              </div>
            ))}
          </dl>
        </section>

        {/* COMEÇAR */}
        <section className={`lp-secao ${comecar.cls}`} ref={comecar.ref}>
          <h2>{t("lp.start.title")}</h2>
          <ol className="lp-passos">
            {PASSOS.map((p, i) => (
              <li key={p}>
                <span className="lp-passo-n tabular">{i + 1}</span>
                <div>
                  <h3>{t(`lp.start.${p}.t`)}</h3>
                  <p>{t(`lp.start.${p}.d`)}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* PREÇO */}
        <section className={`lp-secao ${preco.cls}`} id="preco" ref={preco.ref}>
          <h2>{t("lp.price.title")}</h2>
          <p className="lp-secao-sub">{t("lp.price.sub")}</p>
          <p className="lp-frase">{t("lp.price.soft")}</p>
          <p className="lp-nota">{t("lp.price.note")}</p>
        </section>

        {/* FIM DO TESTE */}
        <section className={`lp-secao ${teste.cls}`} ref={teste.ref}>
          <h2>{t("lp.trial.title")}</h2>
          <p className="lp-secao-sub">{t("lp.trial.sub")}</p>
          <ul className="lp-lista">
            {["read", "discharge", "export", "write"].map((k) => (
              <li key={k} className={k === "write" ? "is-off" : ""}>
                {k === "write" ? <Alert size={16} /> : <Check size={16} />}
                {t(`lp.trial.${k}`)}
              </li>
            ))}
          </ul>
        </section>

        {/* FAQ */}
        <section className={`lp-secao ${faq.cls}`} id="faq" ref={faq.ref}>
          <h2>{t("lp.faq.title")}</h2>
          <div className="lp-faq">
            {FAQS.map((k) => (
              <details key={k}>
                <summary>{t(`lp.faq.${k}.q`)}</summary>
                <p>{t(`lp.faq.${k}.a`)}</p>
              </details>
            ))}
          </div>
        </section>

        {/* CADASTRO */}
        <section className={`lp-criar ${criar.cls}`} id="criar" ref={criar.ref}>
          <div className="lp-criar-texto">
            <h2>{t("lp.final.title")}</h2>
            <p>{t("lp.final.sub")}</p>
          </div>
          <div className="lp-criar-form">
            <SignupForm />
          </div>
        </section>
      </main>

      <footer className="lp-rodape">
        <span>
          Plantão<span className="lp-rodape-vet">Vet</span>
        </span>
        <nav>
          <a href={WHATSAPP_URL} target="_blank" rel="noreferrer">
            {t("lp.footer.talk")}
          </a>
          <Link to="/entrar">{t("lp.nav.signIn")}</Link>
        </nav>
      </footer>
    </div>
  );
}
