import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useLocation } from "react-router-dom";

import { useSession } from "../hooks/useSession";
import { EmptyState, Page } from "./ui";

/** Autorização declarativa, num lugar só.
 *
 *  A regra do produto: quem não pode não vê. Espalhar `can(...)` pelas telas
 *  produziu o que existia antes: sete chamadas no app inteiro, nenhuma no
 *  mobile, e o resto renderizando tudo para todos até bater num 403 depois de
 *  o formulário estar preenchido.
 *
 *  A verdade continua sendo do servidor: isto existe para não OFERECER o que a
 *  API vai recusar, nunca como a checagem em si.
 */
export function Gate({
  can: capability,
  fallback = null,
  children,
}: {
  can: string;
  /** Exceção deliberada: às vezes mostrar desabilitado ENSINA que a
   *  funcionalidade existe. Deve ser exceção, não regra. */
  fallback?: ReactNode;
  children: ReactNode;
}) {
  const { can } = useSession();
  return <>{can(capability) ? children : fallback}</>;
}

/** Guarda de rota.
 *
 *  Os itens de gestão sumiam do menu e as rotas continuavam abertas: bastava
 *  digitar a URL para a tela de equipe renderizar com e-mails e registros, ou a
 *  de configurações com plano e limite de leitos, e o 403 só chegava no
 *  "Salvar". Esconder do menu sem guardar a rota é teatro.
 */
export function RequireCapability({
  can: capability,
  redirectTo = "/",
  children,
}: {
  /** Uma capacidade, ou uma lista: a rota abre se QUALQUER uma da lista for
   *  concedida. Existe por causa de "/gestao" — o portão de fora não pode
   *  exigir só a capacidade de ESCRITA (`clinic.configure`, que some quando o
   *  teste vence) quando uma rota filha (a trilha de auditoria) sobrevive ao
   *  teste vencido por leitura (`audit.read`). Uma string continua
   *  funcionando como sempre: é o caso comum, de todas as outras rotas. */
  can: string | string[];
  redirectTo?: string;
  children: ReactNode;
}) {
  const { can, loading, needsOperator } = useSession();
  const location = useLocation();

  // Enquanto não se sabe quem é, não se decide nada: negar aqui mandaria a
  // pessoa para a home no primeiro render de todo carregamento de página.
  if (loading) return null;

  // Estação sem PIN: a pessoa não é "proibida", é desconhecida. Mandá-la para a
  // home escondia o motivo; pedir o PIN é a resposta honesta.
  if (needsOperator) return <IdentifyFirst />;

  const allowed = Array.isArray(capability) ? capability.some(can) : can(capability);
  if (!allowed) return <Navigate to={redirectTo} replace state={{ from: location }} />;
  return <>{children}</>;
}

function IdentifyFirst() {
  const { t } = useTranslation();
  return (
    <Page title={t("authz.identifyTitle")}>
      <EmptyState title={t("authz.identifyTitle")} hint={t("authz.identifyHint")} />
    </Page>
  );
}

/** Home de cada papel.
 *
 *  O administrador não executa tarefa nem opera turno: mandá-lo para o console
 *  do plantão é abrir um sistema que não tem nada para ele fazer. */
export function RoleHome() {
  const { role, loading } = useSession();
  if (loading) return null;
  return <Navigate to={role === "admin" ? "/internados" : "/plantao"} replace />;
}
