import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router-dom";

import { CAN } from "../api/capabilities";
import { Gate } from "../components/authz";

/** Gestão: a administração sai do caminho de quem cuida de paciente.
 *
 *  Preços, Equipe, Auditoria e Configurações ocupavam quatro dos nove itens da
 *  navegação principal, competindo com paciente e tarefa por atenção o dia
 *  inteiro, para um trabalho que acontece no onboarding e depois quase nunca.
 *  Agora são uma porta só, com sub-navegação própria, visível apenas para quem
 *  administra a clínica.
 *
 *  A escala do plantão entra aqui junto da equipe: montar a escala é gestão de
 *  pessoas. Assumir e encerrar o próprio plantão é operação e vive no console
 *  do turno. Eram duas coisas diferentes numa tela só.
 *
 *  Escala tem aba PRÓPRIA, e não um bloco no fim de Equipe: quem abre Equipe
 *  para trocar o papel de alguém rolava a rota inteira antes de achar a lista,
 *  e quem abre para montar a semana passava pela lista de pessoas primeiro.
 *  Cadastrar gente é do onboarding; montar a rota é toda semana.
 */
const SUB = [
  { to: "equipe", label: "management.team", capability: CAN.teamManage },
  { to: "escala", label: "management.schedule", capability: CAN.shiftSchedule },
  { to: "precos", label: "management.pricing", capability: CAN.priceListManage },
  { to: "aparelhos", label: "management.devices", capability: CAN.clinicConfigure },
  { to: "auditoria", label: "management.audit", capability: CAN.auditRead },
  { to: "configuracoes", label: "management.settings", capability: CAN.clinicConfigure },
] as const;

export function Management() {
  const { t } = useTranslation();
  return (
    <div className="page">
      <header className="page-head">
        <div className="page-head-text">
          <span className="eyebrow">{t("management.eyebrow")}</span>
          <h1 className="page-title">{t("nav.management")}</h1>
        </div>
      </header>
      <nav className="tabs" aria-label={t("nav.management")}>
        {SUB.map((item) => (
          <Gate key={item.to} can={item.capability}>
            <NavLink to={item.to} className="tab">
              {t(item.label)}
            </NavLink>
          </Gate>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
