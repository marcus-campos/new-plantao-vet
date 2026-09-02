import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import "../styles/wall.css";

import { useBoard } from "../hooks/useBoard";
import { useClinic } from "../hooks/useClinic";

/** O painel de parede.
 *
 *  O glossário sempre disse o que ele é: *"Tela somente-leitura com a visão
 *  geral da internação. Uma clínica pode ter zero, um ou vários painéis
 *  abertos: é só uma URL em tela cheia, não um equipamento obrigatório."*
 *
 *  Por isso ele não é uma aba de Internados. Uma aba herdaria a barra de
 *  navegação, a caixa de busca e os botões de ação, e um mural com caixa de
 *  busca não é um mural, é a mesma lista com fonte maior. Aqui não há casca:
 *  a rota vive fora do shell do app, a tipografia é de longe, e a única coisa
 *  clicável é a saída.
 *
 *  Lê a MESMA fila da ficha e do plantão, já ordenada pelo pior primeiro,
 *  nunca uma segunda fonte. */
export function WallBoard() {
  const { t } = useTranslation();
  const { time } = useClinic();
  const { data, error, fetchedAt } = useBoard();

  const rows = data?.rows ?? [];
  const atencao = rows.filter((row) => row.attention);

  return (
    <div className="wall">
      <header className="wall-head">
        <span className="wall-brand">
          Plantão<em>Vet</em>
        </span>
        <span className="wall-headline">
          {atencao.length > 0
            ? t("wall.attention", { count: atencao.length })
            : t("wall.allClear")}
        </span>
        <span className="wall-clock tabular">
          {fetchedAt ? time(fetchedAt) : "—"}
          {error ? <em className="wall-stale">{t("wall.stale")}</em> : null}
        </span>
        <Link to="/internados" className="wall-exit">
          {t("wall.exit")}
        </Link>
      </header>

      <div className="wall-rows">
        {rows.map((row) => {
          const attention = row.attention;
          const grave =
            attention?.reason === "critical_overdue" || attention?.reason === "overdue";
          return (
            <div
              key={row.hospitalization_id}
              className={`wall-row${grave ? " wall-row-late" : attention ? " wall-row-warn" : ""}`}
            >
              <span className="wall-who">
                <strong>{row.patient_name}</strong>
                <span>{row.kennel_name ?? "—"}</span>
              </span>
              <span className="wall-next">
                {row.next_task ? (
                  <>
                    <span className="tabular wall-time">{time(row.next_task.scheduled_for)}</span>
                    <span className="wall-task">{row.next_task.title}</span>
                  </>
                ) : (
                  <span className="wall-task wall-quiet">{t("patients.list.noTask")}</span>
                )}
              </span>
              {/* Nunca só a cor: o estado vem escrito. Numa parede a três
                  metros, e para quem não distingue vermelho de verde, a cor
                  sozinha não informa nada. */}
              <span className="wall-state">
                {attention ? t(`wall.state.${attention.reason}`) : t("wall.state.ok")}
              </span>
              <span className="wall-today tabular">
                {t("census.doneToday", {
                  done: row.counters.done_today,
                  total: row.counters.planned_today,
                })}
              </span>
            </div>
          );
        })}
      </div>

      <footer className="wall-foot">{t("wall.footer")}</footer>
    </div>
  );
}
