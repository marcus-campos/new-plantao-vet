import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";

import "../styles/patient.css";

import { api } from "../api/client";
import { CAN } from "../api/capabilities";
import type { HospitalizationDetail } from "../api/types";
import { Gate } from "../components/authz";
import { Badge, Button, ErrorState, Skeleton, useApiErrorMessage } from "../components/ui";
import { useClinic } from "../hooks/useClinic";

/** O paciente é UM lugar.
 *
 *  Antes eram seis: a ficha tinha seis botões (Evolução, Conta, Tutor,
 *  Prontuário, Alta, Nova prescrição), e todos NAVEGAVAM PARA FORA. Ir ver a
 *  evolução significava perder o contexto do paciente e recarregar tudo ao
 *  voltar. Quem trabalha na internação não pensa "em qual módulo eu entro",
 *  pensa "preciso cuidar desse paciente".
 *
 *  O cabeçalho de contexto (nome, espécie, peso, box, dias internado, vet
 *  responsável e os selos que mudam decisão) fica na tela o tempo inteiro. As
 *  abas trocam só o conteúdo.
 */
interface PatientContextValue {
  detail: HospitalizationDetail;
  reload: () => Promise<void>;
}

const PatientContext = createContext<PatientContextValue | null>(null);

/** O contexto do paciente, para as abas não refazerem a mesma busca. */
export function usePatientContext(): PatientContextValue {
  const context = useContext(PatientContext);
  if (!context) throw new Error("usePatientContext precisa da rota do paciente");
  return context;
}

const TABS = [
  { to: ".", end: true, label: "patient.tab.sheet", capability: null },
  { to: "evolucao", end: false, label: "patient.tab.progress", capability: null },
  { to: "conta", end: false, label: "patient.tab.charges", capability: CAN.chargesRead },
  { to: "prontuario", end: false, label: "patient.tab.record", capability: CAN.recordRead },
  { to: "tutor", end: false, label: "patient.tab.owner", capability: CAN.ownerRead },
] as const;

export function Patient() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();
  const { day, number: weight } = useClinic();
  const describeError = useApiErrorMessage();
  const [detail, setDetail] = useState<HospitalizationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setDetail(await api.hospitalization(id));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [id, describeError]);

  useEffect(() => {
    void reload();
  }, [reload]);

  if (error && !detail) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!detail) return <Skeleton rows={4} />;

  const patient = detail.patient;
  // Dias de internação pelo CALENDÁRIO da clínica, não por milissegundos
  // decorridos: admitido às 23h de ontem, aberto à 01h de hoje, o cabeçalho
  // dizia "1 dia" enquanto o extrato já contava duas diárias.
  const dias = Math.max(1, Math.ceil((Date.now() - new Date(detail.hospitalization.admitted_at).getTime()) / 86_400_000));

  return (
    <PatientContext.Provider value={{ detail, reload }}>
      <div className="patient">
        <header className="patient-head">
          <div className="patient-id">
            <Link to="/internados" className="patient-back">
              {t("patient.backToList")}
            </Link>
            <div className="patient-name-row">
              <h1 className="patient-name">{patient?.name ?? "—"}</h1>
              {detail.kennel_name ? <Badge tone="accent">{detail.kennel_name}</Badge> : null}
            </div>
            <p className="patient-meta">
              {[
                patient?.species,
                patient?.breed,
                patient?.weight_kg ? t("patient.weight", { kg: weight(patient.weight_kg) }) : null,
                t("patient.admittedFor", {
                  count: dias,
                  since: day(detail.hospitalization.admitted_at),
                }),
                detail.vet_name
                  ? `${t("sheet.vet")}: ${detail.vet_name}${detail.vet_license ? ` (${detail.vet_license})` : ""}`
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>

          {/* Só os atos que mudam o paciente ficam aqui. Ver evolução, conta,
              prontuário e tutor viraram abas: são lugares, não ações. */}
          <div className="patient-actions">
            <Gate can={CAN.hospitalizationDischarge}>
              <Link to={`/internacao/${id}/alta`} style={{ textDecoration: "none" }}>
                <Button variant="secondary">{t("sheet.discharge")}</Button>
              </Link>
            </Gate>
            <Gate can={CAN.prescriptionCreate}>
              <Link to={`/internacao/${id}/prescrever`} style={{ textDecoration: "none" }}>
                <Button>{t("sheet.newPrescription")}</Button>
              </Link>
            </Gate>
          </div>
        </header>

        <nav className="tabs" aria-label={t("patient.tabsLabel")}>
          {TABS.map((tab) => (
            <TabLink key={tab.to} tab={tab} />
          ))}
        </nav>

        {error ? <ErrorState message={error} onRetry={() => void reload()} /> : null}
        <Outlet />
      </div>
    </PatientContext.Provider>
  );
}

function TabLink({ tab }: { tab: (typeof TABS)[number] }) {
  const { t } = useTranslation();
  const content = (
    <NavLink
      to={tab.to}
      end={tab.end}
      className="tab"
      // A aba que a pessoa não pode abrir simplesmente não existe: antes o
      // link "Conta" aparecia para o técnico e abria com o extrato inteiro.
    >
      {t(tab.label)}
    </NavLink>
  );
  return tab.capability ? <Gate can={tab.capability}>{content}</Gate> : content;
}
