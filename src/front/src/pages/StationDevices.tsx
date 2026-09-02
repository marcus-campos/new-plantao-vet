import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { StationDevice } from "../api/types";
import { AdminModal, AdminNote, usePinRetry } from "../components/AdminShared";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import "../styles/admin.css";

/** Os aparelhos compartilhados da clínica.
 *
 *  Substitui a chave de estação, que era UMA senha para a clínica inteira.
 *  Três coisas quebravam com ela:
 *
 *  1. Revogar era tudo ou nada. Um tablet sumia e a saída era trocar a chave,
 *     o que derrubava todos os outros aparelhos ao mesmo tempo, no meio do
 *     plantão.
 *  2. Ninguém sabia quais aparelhos existiam. A chave era um texto que
 *     circulava; não havia lista, nome nem "visto pela última vez".
 *  3. Bloquear por erro de PIN não durava nada: o bloqueio vivia na memória
 *     do processo, e relogar zerava a contagem.
 *
 *  Aqui cada aparelho tem nome, segredo próprio e histórico. E o bloqueio por
 *  PIN não expira sozinho: sair dele é ato de um administrador.
 */
export function StationDevices() {
  const { t } = useTranslation();
  const { moment } = useClinic();
  const { run, dialog, error, busy, describeError } = usePinRetry();

  const [devices, setDevices] = useState<StationDevice[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [naming, setNaming] = useState<string | null>(null);
  const [opened, setOpened] = useState<{ code: string; name: string } | null>(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      setDevices(await api.stationDevices());
    } catch (err) {
      setLoadError(describeError(err));
    }
  }, [describeError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function openEnrollment(name: string) {
    await run(async () => {
      const result = await api.openDeviceEnrollment(name.trim());
      setNaming(null);
      setOpened({ code: result.enrollment_code, name: result.device.name });
      await load();
    });
  }

  async function unlock(device: StationDevice) {
    await run(async () => {
      await api.unlockStationDevice(device.id);
      await load();
    });
  }

  async function revoke(device: StationDevice) {
    if (!window.confirm(t("devices.confirmRevoke", { name: device.name }))) return;
    await run(async () => {
      await api.revokeStationDevice(device.id);
      await load();
    });
  }

  const lista = devices ?? [];
  const travados = lista.filter((device) => device.pin_locked_at !== null);

  return (
    <>
      {error ? <ErrorState message={error} /> : null}

      <Section
        title={t("devices.title")}
        hint={t("devices.subtitle")}
        actions={<Button onClick={() => setNaming("")}>{t("devices.new")}</Button>}
      >
        {/* Aparelho travado é a única coisa aqui que alguém está esperando: se
            um tablet parou no meio do plantão, tem gente sem conseguir dar
            baixa agora. Sobe para o topo. */}
        {travados.length > 0 ? (
          <AdminNote tone="danger">
            {t("devices.lockedBanner", { count: travados.length })}
          </AdminNote>
        ) : null}

        {loadError ? <ErrorState message={loadError} onRetry={() => void load()} /> : null}
        {!loadError && devices === null ? <Skeleton rows={3} height={72} /> : null}

        {!loadError && devices !== null && lista.length === 0 ? (
          <EmptyState
            title={t("devices.empty")}
            hint={t("devices.emptyHint")}
            action={<Button onClick={() => setNaming("")}>{t("devices.new")}</Button>}
          />
        ) : null}

        {lista.map((device) => {
          const locked = device.pin_locked_at !== null;
          const pending = device.status === "pending";
          const revoked = device.status === "revoked";
          return (
            <Card key={device.id}>
              <div className="device-row">
                <div className="device-id">
                  <strong>{device.name}</strong>
                  <span className="device-state">
                    {revoked
                      ? t("devices.state.revoked", { when: moment(device.revoked_at ?? "") })
                      : locked
                        ? t("devices.state.locked", {
                            when: moment(device.pin_locked_at ?? ""),
                          })
                        : pending
                          ? t("devices.state.pending")
                          : device.last_seen_at
                            ? t("devices.state.seen", { when: moment(device.last_seen_at) })
                            : t("devices.state.never")}
                  </span>
                  {device.approved_by_name && !revoked ? (
                    <span className="device-by">
                      {t("devices.approvedBy", { name: device.approved_by_name })}
                    </span>
                  ) : null}
                </div>

                <div className="admin-toolbar">
                  {locked ? (
                    <Button disabled={busy} onClick={() => void unlock(device)}>
                      {t("devices.unlock")}
                    </Button>
                  ) : null}
                  {!revoked ? (
                    <Button variant="secondary" disabled={busy} onClick={() => void revoke(device)}>
                      {t("devices.revoke")}
                    </Button>
                  ) : null}
                </div>
              </div>

              {/* O bloqueio não expira: dizer "tente mais tarde" seria mentira,
                  e quem está no aparelho precisa saber que falta um clique
                  daqui, não uma espera de lá. */}
              {locked ? (
                <AdminNote tone="danger">
                  {t("devices.lockedHint", { attempts: device.pin_failed_attempts })}
                </AdminNote>
              ) : null}
            </Card>
          );
        })}

        <p className="admin-footnote">{t("devices.footer")}</p>
      </Section>

      {naming !== null ? (
        <AdminModal title={t("devices.new")} onClose={() => setNaming(null)}>
          <Field label={t("devices.nameLabel")}>
            <input
              style={inputStyle}
              value={naming}
              autoFocus
              placeholder={t("devices.namePlaceholder")}
              onChange={(event) => setNaming(event.target.value)}
            />
            <span className="dose-hint">{t("devices.nameHint")}</span>
          </Field>
          <div className="admin-toolbar">
            <Button disabled={!naming.trim() || busy} onClick={() => void openEnrollment(naming)}>
              {t("devices.generate")}
            </Button>
            <Button variant="secondary" onClick={() => setNaming(null)}>
              {t("common.cancel")}
            </Button>
          </div>
        </AdminModal>
      ) : null}

      {opened ? (
        <AdminModal title={t("devices.codeTitle", { name: opened.name })} onClose={() => setOpened(null)}>
          {/* O código sai da API uma vez só. Quem fechar sem digitar precisa
              abrir outra liberação, e a tela diz isso antes, não depois. */}
          <div className="device-code tabular">{opened.code}</div>
          <p style={{ margin: 0, fontSize: 13.5, color: "var(--ink-2)" }}>
            {t("devices.codeHint")}
          </p>
          <AdminNote>{t("devices.codeWarning")}</AdminNote>
          <Button onClick={() => setOpened(null)}>{t("common.done")}</Button>
        </AdminModal>
      ) : null}

      {dialog}
    </>
  );
}
