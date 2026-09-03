import { Suspense, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, Navigate, Route, Routes, useLocation, useMatch } from "react-router-dom";

import "./styles/app.css";
import "./styles/platform.css";

import { Admission } from "./pages/Admission";
import { AuditTrail } from "./pages/AuditTrail";
import { Charges } from "./pages/Charges";
import { ClinicSettings } from "./pages/ClinicSettings";
import { Discharge } from "./pages/Discharge";
import { Handover } from "./pages/Handover";
import { Inpatients } from "./pages/Inpatients";
import { Login } from "./pages/Login";
import { Management } from "./pages/Management";
import { MedicalRecord } from "./pages/MedicalRecord";
import { NewPrescription } from "./pages/NewPrescription";
import { OwnerContacts } from "./pages/OwnerContacts";
import { Patient } from "./pages/Patient";
import { PriceList } from "./pages/PriceList";
import { ProgressNotes } from "./pages/ProgressNotes";
import { ShiftConsole } from "./pages/ShiftConsole";
import { ShiftSchedule } from "./pages/ShiftSchedule";
import { Signup } from "./pages/Signup";
import { StationDevices } from "./pages/StationDevices";
import { MyPinDialog } from "./components/MyPinDialog";
import { PushButton } from "./components/PushButton";
import { PlatformApp } from "./pages/Platform";
import { Team } from "./pages/Team";
import { TreatmentSheet } from "./pages/TreatmentSheet";
import { WallBoard } from "./pages/WallBoard";
import { CAN } from "./api/capabilities";
import { RequireCapability, RoleHome } from "./components/authz";
import { ClinicProvider } from "./hooks/useClinic";
import { useSession } from "./hooks/useSession";
import { useClinic } from "./hooks/useClinic";
import { useBoard } from "./hooks/useBoard";

export default function App() {
  const { session } = useSession();
  // Sem sessão, a raiz é a landing: é o link que se divulga, e quem chega por
  // ele nunca ouviu falar do produto. Quem já é cliente vai direto para
  // /entrar — e quem tem sessão nunca vê nenhuma das duas, porque o teste de
  // sessão continua vindo antes.
  if (!session)
    return (
      <Routes>
        <Route path="/" element={<Signup />} />
        <Route path="/cadastro" element={<Signup />} />
        <Route path="*" element={<Login />} />
      </Routes>
    );
  // Outra porta, outra casca: quem vende e dá suporte não é membro de clínica
  // nenhuma, e o token da plataforma é recusado por toda rota de clínica. Não
  // há o que montar do shell da clínica para essa sessão.
  if (session.kind === "platform") return <PlatformApp />;
  return (
    <Suspense fallback={null}>
      <ClinicProvider>
        <Routes>
          {/* Fora do shell de propósito: o painel de parede é "só uma URL em
              tela cheia" (CONTEXT.md). Herdar a barra de navegação e a caixa de
              busca faria dele a mesma lista com fonte maior. */}
          <Route path="/painel" element={<WallBoard />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </ClinicProvider>
    </Suspense>
  );
}

/** A navegação inteira.
 *
 *  Eram nove itens nomeados por tabela (Painel, Internados, Passagem, Escala,
 *  Preços, Equipe, Boxes, Auditoria, Configurações) para um trabalho que tem
 *  dois eixos: o tempo e o paciente. Dois deles (Passagem e Escala) apareciam
 *  para o administrador, que não tem nenhuma das ações de lá; os cinco de
 *  gestão sumiam do menu e continuavam acessíveis por URL.
 *
 *  Agora cada item declara a capacidade que exige, e a mesma capacidade guarda
 *  a rota. Ninguém vê o que não pode usar, e ninguém chega por URL onde o menu
 *  não leva.
 *
 *  Onde foi parar o que saiu:
 *  · Painel  → um MODO de Internados (`/internados?vista=mural`), como o
 *              glossário sempre disse ("é só uma URL em tela cheia").
 *  · Boxes   → outro modo de Internados: ocupação é uma lente do censo.
 *  · Escala  → a escala é gestão de equipe; assumir e encerrar o plantão é
 *              operação e vive no console do turno.
 *  · Auditoria → uma lente contextual no paciente e na prescrição, mais a
 *              página global dentro de Gestão. Investigação começa numa coisa,
 *              não num módulo.
 */
const NAV = [
  { to: "/plantao", label: "nav.shift", capability: CAN.taskExecute },
  { to: "/internados", label: "nav.inpatients", capability: null },
  { to: "/passagem", label: "nav.handover", capability: CAN.shiftOperate },
  // Mesma lista do guard de "/gestao": clinic.configure é escrita e some
  // quando o teste vence, mas audit.read sobrevive de propósito. Com só
  // clinic.configure aqui, o menu esconderia uma página que a pessoa ainda
  // pode abrir — exatamente o defeito que este arquivo documenta como
  // corrigido ("ninguém vê o que não pode usar, e ninguém chega por URL onde
  // o menu não leva").
  { to: "/gestao", label: "nav.management", capability: [CAN.clinicConfigure, CAN.auditRead] },
] as const;

function Shell() {
  const { t } = useTranslation();
  const { can } = useSession();
  const location = useLocation();
  const board = useBoard();

  const items = NAV.filter((item) => {
    const capability: string | readonly string[] | null = item.capability;
    if (capability === null) return true;
    // Item de menu com lista de capacidades (caso de "/gestao"): aparece se
    // QUALQUER uma delas for concedida.
    return typeof capability === "string" ? can(capability) : capability.some(can);
  });

  return (
    <div className="app-shell">
      <SubscriptionBanner />
      <header className="app-header">
        <Link to="/" className="brand">
          Plantão<em>Vet</em>
        </Link>

        <nav className="nav-primary" aria-label={t("nav.primary")}>
          {items.map((item) => {
            const active = location.pathname.startsWith(item.to);
            // Só o plantão carrega contador: é a única exceção que merece
            // interromper de fora da tela onde ela mora. Orçamento de alertas.
            const count = item.to === "/plantao" ? (board.data?.totals.attention ?? 0) : 0;
            return (
              <Link
                key={item.to}
                to={item.to}
                className="nav-link"
                aria-current={active ? "page" : undefined}
              >
                {t(item.label)}
                {count > 0 ? (
                  <span className="nav-count" aria-label={t("nav.attentionCount", { n: count })}>
                    {count}
                  </span>
                ) : null}
              </Link>
            );
          })}
        </nav>

        <Identity />
      </header>

      <main className="app-main">
        <Routes>
          <Route path="/" element={<RoleHome />} />
          <Route
            path="/plantao"
            element={
              <RequireCapability can={CAN.taskExecute} redirectTo="/internados">
                <ShiftConsole />
              </RequireCapability>
            }
          />
          <Route path="/internados" element={<Inpatients />} />
          <Route path="/internar" element={<Admission />} />
          <Route
            path="/passagem"
            element={
              <RequireCapability can={CAN.shiftOperate} redirectTo="/internados">
                <Handover />
              </RequireCapability>
            }
          />

          {/* O paciente é UM lugar com abas, não seis páginas que se perdem de
              vista. O cabeçalho de contexto (nome, box, dias, vet, selos) não
              sai da tela enquanto se trabalha nele. */}
          <Route path="/internacao/:id" element={<Patient />}>
            <Route index element={<TreatmentSheet />} />
            <Route path="evolucao" element={<ProgressNotes />} />
            <Route
              path="conta"
              element={
                <RequireCapability can={CAN.chargesRead} redirectTo=".">
                  <Charges />
                </RequireCapability>
              }
            />
            <Route path="prontuario" element={<MedicalRecord />} />
            <Route path="tutor" element={<OwnerContacts />} />
          </Route>
          {/* Prescrever e dar alta são atos privativos e ocupam a tela inteira:
              a guarda de rota espelha o botão que os oferece. */}
          <Route
            path="/internacao/:id/prescrever"
            element={
              <RequireCapability can={CAN.prescriptionCreate}>
                <NewPrescription />
              </RequireCapability>
            }
          />
          <Route
            path="/internacao/:id/alta"
            element={
              <RequireCapability can={CAN.hospitalizationDischarge}>
                <Discharge />
              </RequireCapability>
            }
          />

          <Route
            path="/gestao"
            element={
              // clinic.configure é escrita e some da lista quando o teste vence;
              // se o portão exigisse só ela, a trilha de auditoria — rota filha,
              // que sobrevive ao teste vencido por causa de audit.read — ficaria
              // trancada atrás de uma exigência que ela não precisa cumprir.
              // Cada aba dentro de Management já se guarda pela própria
              // capacidade (ver SUB em Management.tsx); este portão só decide
              // quem entra no prédio, não o que se faz lá dentro.
              <RequireCapability can={[CAN.clinicConfigure, CAN.auditRead]} redirectTo="/internados">
                <Management />
              </RequireCapability>
            }
          >
            <Route index element={<Navigate to="equipe" replace />} />
            {/* "Esconder do menu sem guardar a rota é teatro" (authz.tsx): o
                portão do PAI só decide quem entra no prédio — aceita
                clinicConfigure OU auditRead —, mas cada sala continua exigindo
                a chave própria. Sem isso, um vet (que tem auditRead e por
                tanto atravessa o portão) digitaria a URL de Equipe, Preços,
                Aparelhos ou Configurações e veria a tela inteira renderizar,
                porque o <Gate> de Management.tsx só filtra o LINK da aba, não
                a rota casada pelo <Outlet/>. redirectTo aponta para Auditoria
                porque é a única aba que sobrevive ao teste vencido e que,
                portanto, qualquer um que passou pelo portão do pai consegue
                abrir — clinicConfigure e auditRead nunca faltam os dois ao
                mesmo tempo (admin tem os dois; vet só tem auditRead). */}
            <Route
              path="equipe"
              element={
                <RequireCapability can={CAN.teamManage} redirectTo="/gestao/auditoria">
                  <Team />
                </RequireCapability>
              }
            />
            <Route
              path="escala"
              element={
                <RequireCapability can={CAN.shiftSchedule} redirectTo="/gestao/auditoria">
                  <ShiftSchedule />
                </RequireCapability>
              }
            />
            <Route
              path="precos"
              element={
                <RequireCapability can={CAN.priceListManage} redirectTo="/gestao/auditoria">
                  <PriceList />
                </RequireCapability>
              }
            />
            <Route
              path="aparelhos"
              element={
                <RequireCapability can={CAN.clinicConfigure} redirectTo="/gestao/auditoria">
                  <StationDevices />
                </RequireCapability>
              }
            />
            <Route
              path="auditoria"
              element={
                // Guarda por si mesma, e não só pelo portão do pai: manter o
                // padrão explícito de cada filha, mesmo sabendo que quem
                // atravessou o portão sempre tem auditRead ou clinicConfigure
                // (e admin tem os dois). redirectTo cai fora de Gestão — não
                // há outra aba universalmente segura para onde mandar aqui.
                <RequireCapability can={CAN.auditRead} redirectTo="/internados">
                  <AuditTrail />
                </RequireCapability>
              }
            />
            <Route
              path="configuracoes"
              element={
                <RequireCapability can={CAN.clinicConfigure} redirectTo="/gestao/auditoria">
                  <ClinicSettings />
                </RequireCapability>
              }
            />
          </Route>

          {/* Rotas antigas continuam funcionando: link salvo e favorito não
              podem virar 404 por causa de uma reorganização nossa. */}
          <Route path="/pacientes" element={<Navigate to="/internados" replace />} />
          <Route path="/boxes" element={<Navigate to="/internados?vista=boxes" replace />} />
          <Route path="/escala" element={<Navigate to="/gestao/escala" replace />} />
          <Route path="/precos" element={<Navigate to="/gestao/precos" replace />} />
          <Route path="/equipe" element={<Navigate to="/gestao/equipe" replace />} />
          <Route path="/auditoria" element={<Navigate to="/gestao/auditoria" replace />} />
          <Route
            path="/configuracoes"
            element={<Navigate to="/gestao/configuracoes" replace />}
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

/** Quem responde pelos atos agora.
 *
 *  Na estação isto não é enfeite: sem alguém identificado o aparelho não pode
 *  oferecer quase nada, e a interface precisa dizer isso em vez de mostrar
 *  botões que vão falhar. O rótulo era `"PIN ativo"` / `"Modo estação"` em
 *  português cravado no componente, a única violação real do ADR-0004. */
function Identity() {
  const { t } = useTranslation();
  const { session, me, actorName, clearOperator, logout, needsOperator } = useSession();
  const isStation = session?.kind === "station";
  const [pinOpen, setPinOpen] = useState(false);

  return (
    <div className="identity">
      {isStation ? (
        <button
          type="button"
          className={`identity-chip${needsOperator ? " identity-chip-anon" : ""}`}
          onClick={clearOperator}
        >
          {needsOperator ? t("identity.station") : t("identity.operator", { name: actorName })}
        </button>
      ) : (
        // O PIN é de quem está logado, então mora ao lado de quem está
        // logado. Na estação não existe "meu PIN": quem responde ali é o
        // operador do momento, e a sessão é do aparelho.
        <>
          <PushButton />
          <button type="button" className="nav-link" onClick={() => setPinOpen(true)}>
            {t("mypin.open")}
          </button>
        </>
      )}
      <button type="button" className="nav-link" onClick={logout}>
        {t("nav.logout")}
      </button>
      {pinOpen ? (
        <MyPinDialog hasPin={me?.has_pin ?? true} onClose={() => setPinOpen(false)} />
      ) : null}
    </div>
  );
}

/** Teste acabando ou boleto em atraso: uma linha no topo, para o administrador.
 *
 *  Só ele: o técnico no meio do plantão não tem o que fazer com "sua assinatura
 *  vence em 3 dias", e um aviso que a pessoa não pode resolver é ruído. Nunca
 *  bloqueia nada: suspensão fecha a porta no login, e a interface do cliente
 *  não decide isso. */
function SubscriptionBanner() {
  const { t } = useTranslation();
  const { can, me } = useSession();
  const { profile } = useClinic();

  // O vencido vem ANTES da guarda de capacidade, e para todo mundo: quem
  // acabou de perder o botão de dar baixa precisa saber por quê, e
  // `clinic.configure` é escrita — some da lista justamente agora, o que
  // esconderia este aviso de quem mais precisa dele.
  if (me?.read_only) {
    return (
      <div className="subscription-banner subscription-banner-expired" role="status">
        {t("subscription.expired")}{" "}
        <a
          href="https://wa.me/5561983031823?text=Ol%C3%A1%21%20Meu%20teste%20do%20Plant%C3%A3oVet%20terminou"
          target="_blank"
          rel="noreferrer"
        >
          {t("subscription.expiredCta")}
        </a>
      </div>
    );
  }

  if (!profile || !can(CAN.clinicConfigure)) return null;
  if (profile.subscription_status === "past_due") {
    return <div className="subscription-banner subscription-banner-late">{t("subscription.pastDue")}</div>;
  }
  if (profile.subscription_status === "trial" && profile.trial_ends_at) {
    const days = Math.max(0, Math.ceil((Date.parse(profile.trial_ends_at) - Date.now()) / 86_400_000));
    if (days > 14) return null;
    return <div className="subscription-banner">{t("subscription.trialEnding", { count: days })}</div>;
  }
  return null;
}

/** Rota ativa dentro do paciente. Usado pelas abas. */
export function usePatientTab(): string {
  const match = useMatch("/internacao/:id/*");
  return match?.params["*"] || "";
}
