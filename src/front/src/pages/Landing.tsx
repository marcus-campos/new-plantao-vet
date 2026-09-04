import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { SignupForm } from "../components/SignupForm";
import "../styles/landing.css";

const WHATSAPP_URL =
  "https://wa.me/5561983031823?text=Ol%C3%A1%21%20Quero%20saber%20mais%20sobre%20o%20Plant%C3%A3oVet";

function useReducedMotion() {
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

type Marca = "feita" | "programada" | "atrasada";

const HORAS = ["18", "19", "20", "21", "22", "23", "00", "01", "02"];

const LEITOS: { nome: string; meta: string; doses: Record<string, Marca> }[] = [
  {
    nome: "Thor",
    meta: "canino · UTI 03",
    doses: { "18": "feita", "20": "feita", "22": "atrasada", "02": "programada" },
  },
  {
    nome: "Mel",
    meta: "felina · Box 07",
    doses: { "18": "feita", "21": "feita", "23": "programada", "02": "programada" },
  },
  {
    nome: "Amora",
    meta: "canina · Box 02",
    doses: { "19": "feita", "22": "feita", "01": "programada" },
  },
];

const ROTULO: Record<Marca, string> = {
  feita: "dose registrada",
  programada: "dose programada",
  atrasada: "dose atrasada",
};

function GradeHoras() {
  const { t } = useTranslation();
  const reduced = useReducedMotion();
  const [entrou, setEntrou] = useState(false);
  const corpo = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (reduced) return;
    const id = requestAnimationFrame(() => setEntrou(true));
    return () => cancelAnimationFrame(id);
  }, [reduced]);

  useEffect(() => {
    const alvo = corpo.current;
    if (!alvo) return;
    const excedente = alvo.scrollWidth - alvo.clientWidth;
    if (excedente <= 0) return;
    const agora = alvo.querySelector<HTMLElement>(".lp-agora");
    if (!agora) return;
    const desejado = agora.offsetLeft - alvo.clientWidth * 0.55;
    alvo.scrollLeft = Math.max(0, Math.min(desejado, excedente));
  }, []);

  const classes = ["lp-grade", reduced ? "" : "is-armada", entrou ? "is-in" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <figure className={classes}>
      <div className="lp-grade-topo">
        <span className="lp-grade-local">
          Internação
          <small>3 pacientes · plantão 19h às 07h</small>
        </span>
        <span className="lp-grade-relogio">22:03</span>
      </div>

      <div className="lp-grade-corpo" ref={corpo}>
        <div className="lp-grade-pista">
          <table className="lp-grade-tab">
            <caption className="lp-oculto">{t("lp.sheet.aria")}</caption>
            <thead>
              <tr>
                <th scope="col">Paciente</th>
                {HORAS.map((h) => (
                  <th key={h} scope="col">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {LEITOS.map((leito) => (
                <tr key={leito.nome}>
                  <th scope="row" className="lp-grade-paciente">
                    {leito.nome}
                    <small>{leito.meta}</small>
                  </th>
                  {HORAS.map((h, i) => {
                    const marca = leito.doses[h];
                    return (
                      <td key={h} className={marca === "atrasada" ? "lp-celula-atrasada" : undefined}>
                        {marca ? (
                          <span
                            className={`lp-dose lp-dose-${marca}`}
                            style={{ "--i": i } as CSSProperties}
                          >
                            {marca === "feita" ? <Check size={15} /> : null}
                            {marca === "atrasada" ? <Alert size={15} /> : null}
                            <span className="lp-oculto">
                              {h}:00, {ROTULO[marca]}
                            </span>
                          </span>
                        ) : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <span
            className="lp-agora"
            style={{ left: "calc(var(--rotulo) + 4.05 * (100% - var(--rotulo)) / 9)" }}
            aria-hidden="true"
          />
        </div>
      </div>

      <figcaption className="lp-grade-pe">
        <Alert size={16} />
        {t("lp.sheet.footLate")}
      </figcaption>
    </figure>
  );
}

function FluxoPrescricao() {
  const { t } = useTranslation();

  return (
    <div className="lp-fluxo">
      <div className="lp-fluxo-passo">
        <h3>{t("lp.flow.a.title")}</h3>
        <div className="lp-receita">
          <strong>Dipirona</strong>
          <span>25 mg/kg</span>
          <span>IV</span>
          <span className="lp-freq">q8h</span>
        </div>
        <p>{t("lp.flow.a.body")}</p>
      </div>

      <div className="lp-fluxo-passo">
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

      <div className="lp-fluxo-passo">
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

function FluxoPassagem() {
  const { t } = useTranslation();
  const itens = ["done", "notDone", "pending", "changes"] as const;
  return (
    <div className="lp-passagem">
      <div className="lp-turno lp-turno-sai">
        <span className="lp-turno-rot">{t("lp.handover.out")}</span>
        <strong>07h às 19h</strong>
      </div>

      <div className="lp-passagem-meio">
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
      </div>

      <div className="lp-turno lp-turno-entra">
        <span className="lp-turno-rot">{t("lp.handover.in")}</span>
        <strong>19h às 07h</strong>
      </div>
    </div>
  );
}

const SUPERFICIES = ["web", "board", "app", "station"] as const;
const FAQS = ["erp", "record", "mobile", "install", "beds", "over", "after", "export", "who", "team"] as const;
const PASSOS = ["a", "b", "c"] as const;
const JANELAS = [
  { k: "critical", min: 30, tom: "lp-janela-critica" },
  { k: "normal", min: 60, tom: "lp-janela-media" },
  { k: "daily", min: 120, tom: "" },
] as const;

export function Landing() {
  const { t } = useTranslation();
  const [colado, setColado] = useState(false);

  useEffect(() => {
    const aoRolar = () => setColado(window.scrollY > 8);
    aoRolar();
    window.addEventListener("scroll", aoRolar, { passive: true });
    return () => window.removeEventListener("scroll", aoRolar);
  }, []);

  return (
    <div className="lp">
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
        <section className="lp-hero">
          <span className="lp-hora">22:03</span>
          <div className="lp-hero-texto">
            <h1>{t("lp.hero.title")}</h1>
            <p className="lp-hero-sub">{t("lp.hero.sub")}</p>
            <div className="lp-hero-acoes">
              <a href="#criar" className="lp-btn lp-btn-primario">
                {t("lp.cta.primary")}
              </a>
              <a href="#fluxo" className="lp-btn lp-btn-fantasma">
                {t("lp.cta.secondary")}
              </a>
            </div>
            <p className="lp-hero-nota">{t("lp.hero.note")}</p>
          </div>
          <div className="lp-hero-produto">
            <GradeHoras />
          </div>
        </section>

        <section className="lp-cena">
          <p className="lp-cena-linha">{t("lp.scene.a")}</p>
          <p className="lp-cena-linha">{t("lp.scene.b")}</p>
          <p className="lp-cena-linha">{t("lp.scene.c")}</p>
          <p className="lp-cena-virada">{t("lp.scene.turn")}</p>
        </section>

        <section className="lp-secao" id="fluxo">
          <span className="lp-hora">10:00</span>
          <h2>{t("lp.flow.title")}</h2>
          <p className="lp-secao-sub">{t("lp.flow.sub")}</p>
          <FluxoPrescricao />
        </section>

        <section className="lp-secao">
          <span className="lp-hora">22:03</span>
          <h2>{t("lp.late.title")}</h2>
          <p className="lp-secao-sub">{t("lp.late.sub")}</p>
          <div className="lp-janelas">
            {JANELAS.map(({ k, min, tom }) => (
              <div key={k} className={`lp-janela ${tom}`}>
                <span className="lp-janela-nome">{t(`lp.late.${k}`)}</span>
                <span className="lp-janela-barra">
                  <span className="lp-janela-trilho">
                    <i style={{ width: `${(min / 120) * 100}%` }} />
                  </span>
                  <span className="lp-janela-min tabular">
                    {min} <span className="lp-janela-un">min</span>
                  </span>
                </span>
              </div>
            ))}
          </div>
          <p className="lp-nota">{t("lp.late.note")}</p>
        </section>

        <section className="lp-secao" id="passagem">
          <span className="lp-hora">19:00</span>
          <h2>{t("lp.handover.title")}</h2>
          <p className="lp-secao-sub">{t("lp.handover.sub")}</p>
          <FluxoPassagem />
          <p className="lp-frase">{t("lp.handover.line")}</p>
        </section>

        <section className="lp-secao" id="como">
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

        <section className="lp-estacao">
          <span className="lp-hora">02:00</span>
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

        <section className="lp-secao">
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

        <section className="lp-secao">
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

        <section className="lp-secao" id="preco">
          <h2>{t("lp.price.title")}</h2>
          <p className="lp-secao-sub">{t("lp.price.sub")}</p>
          <p className="lp-frase">{t("lp.price.soft")}</p>
          <p className="lp-nota">{t("lp.price.note")}</p>
        </section>

        <section className="lp-secao">
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

        <section className="lp-secao" id="faq">
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

        <section className="lp-criar" id="criar">
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
