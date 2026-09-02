import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, asList } from "../api/client";
import type { ContactChannel, Owner, OwnerContact } from "../api/types";
import { PinDialog } from "../components/PinDialog";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  ErrorState,
  Field,
  Section,
  Skeleton,
  inputStyle,
  useApiErrorMessage,
} from "../components/ui";
import { useClinic } from "../hooks/useClinic";
import { usePatientContext } from "./Patient";
import "../styles/billing.css";

const CHANNELS: ContactChannel[] = ["phone", "whatsapp", "in_person"];

/** Ação pendente de PIN: no modo estação a mutação volta com operator_required. */
type Pending = "contact" | "bulletin" | "optin" | null;

/** Comunicação com o tutor.
 *
 *  É uma aba do paciente: nome, box, espécie e vet responsável são do cabeçalho
 *  de `Patient.tsx`. Aqui fica o que se falou com o tutor e o que se pode falar.
 */
export function OwnerContacts() {
  const { detail } = usePatientContext();
  const { t } = useTranslation();
  const { moment, day, time } = useClinic();
  const describeError = useApiErrorMessage();
  const hospitalizationId = detail.hospitalization.id;
  const ownerId = detail.patient?.owner_id ?? null;

  const [contacts, setContacts] = useState<OwnerContact[] | null>(null);
  const [owner, setOwner] = useState<Owner | null>(null);
  const [ownerError, setOwnerError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [optInBlocked, setOptInBlocked] = useState(false);
  const [consentConfirmed, setConsentConfirmed] = useState(false);
  const [pending, setPending] = useState<Pending>(null);
  const [askPin, setAskPin] = useState(false);
  const [busy, setBusy] = useState(false);

  const [channel, setChannel] = useState<ContactChannel>("phone");
  const [summary, setSummary] = useState("");
  const [draft, setDraft] = useState("");
  const [draftTouched, setDraftTouched] = useState(false);

  const loadContacts = useCallback(async () => {
    try {
      setContacts(asList(await api.ownerContacts(hospitalizationId)));
      setError(null);
    } catch (err) {
      setError(describeError(err));
    }
  }, [hospitalizationId, describeError]);

  useEffect(() => {
    void loadContacts();
  }, [loadContacts]);

  /** O cadastro do tutor desta internação.
   *
   *  Antes a tela baixava `patients()` E `owners()` INTEIROS (limite 50, cursor
   *  ignorado) e cruzava no navegador: numa clínica com 51 pacientes o tutor
   *  simplesmente não era encontrado e o nome saía em branco, como se o
   *  cadastro estivesse vazio. Agora o vínculo vem da própria ficha
   *  (`patient.owner_id`) e só resta procurar o cadastro na página que a API
   *  devolve: não há rota de tutor por id no cliente. Não achar é dado que
   *  falta, e a tela diz isso em vez de mostrar um branco. */
  const loadOwner = useCallback(async () => {
    if (!ownerId) {
      setOwnerError(t("owner.lookupFailed"));
      return;
    }
    try {
      const found = asList(await api.owners()).find((row) => row.id === ownerId) ?? null;
      setOwner(found);
      setOwnerError(found ? null : t("owner.lookupFailed"));
    } catch (err) {
      setOwnerError(describeError(err));
    }
  }, [ownerId, describeError, t]);

  useEffect(() => {
    void loadOwner();
  }, [loadOwner]);

  /** Rascunho em linguagem simples, montado do que a equipe já registrou hoje. */
  const seed = useMemo(() => {
    const now = new Date();
    const patient = detail.patient?.name ?? "";
    // "Hoje" é o dia da CLÍNICA: `moment()` só omite a data quando o instante
    // cai no dia de hoje no fuso dela, então a igualdade com `time()` é o
    // mesmo dia civil da clínica, sem refazer a conta com o relógio do
    // aparelho, que numa estação fora do fuso contava o dia errado.
    const isToday = (iso: string) => moment(iso) === time(iso);
    const doneToday = detail.tasks.filter(
      (task) =>
        task.executed_at !== null &&
        (task.status === "done" || task.status === "partial") &&
        isToday(task.executed_at),
    ).length;
    const next = detail.tasks
      .filter((task) => task.status === "pending" && new Date(task.scheduled_for) > now)
      .sort((a, b) => new Date(a.scheduled_for).getTime() - new Date(b.scheduled_for).getTime())[0];

    // Saudação neutra: a de "bom dia / boa tarde" saía do relógio do APARELHO e
    // desejava bom dia às 21h da clínica num quiosque em outro fuso. O
    // formatador da clínica devolve texto, não hora; então a mensagem usa uma
    // abertura que está certa a qualquer hora.
    const greeting = t("owner.draft.greeting.neutral");

    return [
      owner?.name
        ? t("owner.draft.opening", { greeting, owner: owner.name, patient })
        : t("owner.draft.openingNoName", { greeting, patient }),
      doneToday > 0 ? t("owner.draft.doneToday", { times: doneToday }) : t("owner.draft.nothingYet"),
      next ? t("owner.draft.next", { time: time(next.scheduled_for) }) : null,
      t("owner.draft.closing"),
    ]
      .filter(Boolean)
      .join(" ");
  }, [detail.patient, detail.tasks, owner, t, moment, time]);

  useEffect(() => {
    if (!draftTouched) setDraft(seed);
  }, [seed, draftTouched]);

  const submitContact = useCallback(async () => {
    if (!summary.trim()) {
      setError(t("owner.form.invalid"));
      return;
    }
    setBusy(true);
    try {
      await api.createOwnerContact(hospitalizationId, {
        channel,
        direction: "outbound",
        summary: summary.trim(),
      });
      setSummary("");
      setError(null);
      setPending(null);
      await loadContacts();
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setPending("contact");
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [hospitalizationId, channel, summary, loadContacts, describeError, t]);

  /** Registra o aceite do tutor.
   *
   *  A tela dizia "o tutor precisa autorizar" e não oferecia nenhum lugar para
   *  registrar o aceite: o boletim ficava preso em 409 para sempre, com
   *  `api.setWhatsAppOptIn` existindo e sem nenhum chamador. Não é um
   *  interruptor de preferência: é um registro de consentimento (LGPD e termos
   *  da Meta), por isso exige a confirmação explícita de quem registra. */
  const recordOptIn = useCallback(async () => {
    if (!ownerId) return;
    setBusy(true);
    try {
      setOwner(await api.setWhatsAppOptIn(ownerId));
      setOptInBlocked(false);
      setConsentConfirmed(false);
      setError(null);
      setPending(null);
      setNotice(t("owner.optIn.recorded"));
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setPending("optin");
        setAskPin(true);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [ownerId, describeError, t]);

  const sendBulletin = useCallback(async () => {
    if (!draft.trim()) {
      setError(t("owner.form.invalid"));
      return;
    }
    setBusy(true);
    try {
      await api.sendWhatsAppBulletin(hospitalizationId, { body: draft.trim() });
      setOptInBlocked(false);
      setError(null);
      // "Enviado" seria promessa: o cliente de WhatsApp do servidor ainda é um
      // stub que devolve um id sintético, e a rota grava `sent_at` de qualquer
      // jeito. O que se pode afirmar é que o boletim ficou registrado.
      setNotice(t("owner.bulletin.queued"));
      setPending(null);
      setDraftTouched(false);
      await loadContacts();
    } catch (err) {
      if (err instanceof ApiError && err.code === "operator_required") {
        setPending("bulletin");
        setAskPin(true);
        return;
      }
      if (err instanceof ApiError && err.code === "whatsapp_opt_in_required") {
        // Não é erro genérico: falta o aceite do tutor, e sem ele a Meta e a
        // LGPD proíbem o envio. A tela diz o que fazer, não "algo deu errado".
        setOptInBlocked(true);
        setError(null);
        return;
      }
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  }, [hospitalizationId, draft, loadContacts, describeError, t]);

  const ownerName = owner?.name ?? null;
  const optInMissing = optInBlocked || (owner !== null && owner.whatsapp_opt_in_at === null);
  const optInDate = owner?.whatsapp_opt_in_at ?? null;
  const subjectName = ownerName ?? t("owner.subjectFallback");

  return (
    <>
      <ErrorBanner message={error} />

      <div className="owner-layout">
        <div className="owner-column">
          {/* `delivered_at` e `read_at` existiam como colunas e nunca são
              escritos: não há webhook da Meta neste produto. Uma coluna que
              nunca preenche ensina que a entrega falhou: some até existir. */}
          <Section title={t("owner.history")} hint={t("owner.deliveryNote")}>
            {contacts === null ? (
              <Skeleton rows={2} height={72} />
            ) : contacts.length === 0 ? (
              <EmptyState title={t("owner.empty")} hint={t("owner.form.summaryHint")} />
            ) : (
              <Card style={{ padding: 0 }}>
                {contacts.map((contact) => (
                  <article key={contact.id} className="owner-contact">
                    <span className="owner-avatar" aria-hidden="true">
                      <ChannelIcon channel={contact.channel} />
                    </span>
                    <div className="owner-contact-body">
                      <div className="owner-contact-head">
                        <strong style={{ fontSize: 14.5 }}>
                          {t(`owner.channel.${contact.channel}`)} ·{" "}
                          {t("owner.by", { name: contact.author_name })}
                        </strong>
                        <span className="tabular" style={{ fontSize: 12.5, color: "var(--ink-3)" }}>
                          {t(`owner.direction.${contact.direction}`)}
                        </span>
                      </div>
                      <p
                        style={{
                          margin: 0,
                          fontSize: 13.5,
                          color: "var(--ink-2)",
                          lineHeight: 1.5,
                        }}
                      >
                        {contact.summary}
                      </p>
                      <div className="owner-stamps tabular">
                        <span>
                          {t("owner.stamp.recorded", { when: moment(contact.sent_at) })}
                        </span>
                      </div>
                    </div>
                  </article>
                ))}
              </Card>
            )}
          </Section>
        </div>

        <div className="owner-column">
          <Section title={t("owner.register")}>
            <Card style={{ display: "grid", gap: 12 }}>
              <Field label={t("owner.form.channel")}>
                <div className="chip-group">
                  {CHANNELS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`chip${channel === option ? " chip-on" : ""}`}
                      onClick={() => setChannel(option)}
                    >
                      {t(`owner.channel.${option}`)}
                    </button>
                  ))}
                </div>
              </Field>

              <Field label={t("owner.form.summary")}>
                <textarea
                  style={{ ...inputStyle, minHeight: 96, resize: "vertical" }}
                  value={summary}
                  onChange={(event) => setSummary(event.target.value)}
                />
              </Field>

              <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>
                {t("owner.form.summaryHint")}
              </p>

              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <Button onClick={() => void submitContact()} disabled={busy}>
                  {t("owner.form.submit")}
                </Button>
              </div>
            </Card>
          </Section>

          <Section title={t("owner.bulletin.title")}>
            {ownerError ? (
              <ErrorState message={ownerError} onRetry={() => void loadOwner()} />
            ) : null}

            {optInMissing ? (
              <div className="owner-notice owner-notice-warn" role="alert">
                <div>
                  <strong style={{ display: "block" }}>{t("owner.optIn.required")}</strong>
                  {t("owner.optIn.blocked", { name: subjectName })}
                </div>
              </div>
            ) : optInDate ? (
              <div className="owner-notice owner-notice-info">
                {t("owner.optIn.active", { date: day(optInDate) })}
              </div>
            ) : (
              <div className="owner-notice owner-notice-info">{t("owner.optIn.unknown")}</div>
            )}

            {optInMissing && ownerId ? (
              <Card style={{ display: "grid", gap: 10 }}>
                <strong style={{ fontSize: 14.5 }}>{t("owner.optIn.recordTitle")}</strong>
                <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)", lineHeight: 1.5 }}>
                  {t("owner.optIn.recordBody", { name: subjectName })}
                </p>
                <label className="record-check">
                  <input
                    type="checkbox"
                    checked={consentConfirmed}
                    onChange={(event) => setConsentConfirmed(event.target.checked)}
                  />
                  {t("owner.optIn.confirmLabel", { name: subjectName })}
                </label>
                <div style={{ display: "flex", justifyContent: "flex-end" }}>
                  <Button onClick={() => void recordOptIn()} disabled={!consentConfirmed || busy}>
                    {t("owner.optIn.recordAction")}
                  </Button>
                </div>
              </Card>
            ) : null}

            <Card style={{ display: "grid", gap: 12 }}>
              <Field label={t("owner.bulletin.draftLabel")}>
                <textarea
                  className="owner-draft"
                  value={draft}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setDraftTouched(true);
                  }}
                />
              </Field>

              <p style={{ margin: 0, fontSize: 12.5, color: "var(--ink-3)" }}>
                {t("owner.bulletin.hint")}
              </p>

              {notice ? (
                <p style={{ margin: 0, fontSize: 13, color: "var(--ok)", fontWeight: 600 }}>
                  {notice}
                </p>
              ) : null}

              <div
                style={{ display: "flex", gap: 10, justifyContent: "flex-end", flexWrap: "wrap" }}
              >
                <Button
                  variant="secondary"
                  onClick={() => {
                    setDraftTouched(false);
                    setDraft(seed);
                    setNotice(null);
                  }}
                  disabled={busy}
                >
                  {t("owner.bulletin.rebuild")}
                </Button>
                <Button onClick={() => void sendBulletin()} disabled={busy || optInMissing}>
                  {t("owner.bulletin.send")}
                </Button>
              </div>
            </Card>

            <div className="owner-notice owner-notice-info">{t("owner.bulletin.cost")}</div>
            <p className="billing-footnote">{t("owner.bulletin.deliveryNote")}</p>
          </Section>
        </div>
      </div>

      <p className="billing-footnote">{t("owner.footer")}</p>

      {askPin ? (
        <PinDialog
          context={
            pending === "bulletin"
              ? t("owner.bulletin.title")
              : pending === "optin"
                ? t("owner.optIn.recordTitle")
                : t("owner.register")
          }
          onDone={() => {
            setAskPin(false);
            const action = pending;
            setPending(null);
            if (action === "bulletin") void sendBulletin();
            if (action === "contact") void submitContact();
            if (action === "optin") void recordOptIn();
          }}
          onCancel={() => {
            setAskPin(false);
            setPending(null);
          }}
        />
      ) : null}
    </>
  );
}

function ChannelIcon({ channel }: { channel: ContactChannel }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "var(--primary-dark)",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  if (channel === "whatsapp") {
    return (
      <svg {...common}>
        <path d="M21 11.5a8.4 8.4 0 0 1-8.4 8.4c-1.5 0-2.9-.38-4.1-1L3 20l1.2-5.3A8.4 8.4 0 1 1 21 11.5z" />
      </svg>
    );
  }
  if (channel === "in_person") {
    return (
      <svg {...common}>
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
        <circle cx="12" cy="7" r="4" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.13 1 .36 1.9.7 2.8a2 2 0 0 1-.45 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.45c.9.34 1.8.57 2.8.7a2 2 0 0 1 1.7 2z" />
    </svg>
  );
}
