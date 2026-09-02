import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import type { ClinicProfile } from "../api/types";

/** O relógio e a régua da clínica.
 *
 *  Todo `scheduled_for` é calculado no fuso da CLÍNICA (`SchedulingService`
 *  resolve as âncoras em `ZoneInfo(clinic.timezone)`), e o cliente formatava
 *  tudo no fuso do APARELHO: nenhuma das 28 chamadas de `Intl.DateTimeFormat`
 *  passava `timeZone`. Um quiosque em UTC mostrava a dose das 10h como 13h, sem
 *  nenhum aviso, num produto cujo trabalho inteiro é a hora certa.
 *
 *  Mesma história com dinheiro: `"BRL"` estava fixo em cinco arquivos, enquanto
 *  as configurações ofereciam USD e EUR.
 */
interface ClinicContextValue {
  profile: ClinicProfile | null;
  timezone: string;
  currency: string;
  /** Hora do relógio da clínica: "18:05". */
  time: (iso: string | Date) => string;
  /** Dia e hora quando não é hoje: "31/08 18:05"; só a hora quando é. */
  moment: (iso: string | Date) => string;
  /** Dia por extenso: "domingo, 31 de agosto". */
  day: (iso: string | Date) => string;
  /** Dinheiro na moeda da clínica, a partir da unidade menor (centavos). */
  money: (minor: number | null | undefined) => string;
  /** Número no locale da clínica. A API devolve Decimal como STRING ("3.600"),
   *  e concatenar cru fazia um gato de 3,6 kg aparecer como "3.600 kg". */
  number: (value: string | number | null | undefined, maxFractionDigits?: number) => string;
  /** O separador decimal do locale da clínica: "," em pt-BR, "." em en-US. */
  decimalSeparator: string;
  /** "há 2h10" / "em 40 min": a magnitude, que o produto só sabia expressar
   *  para trás e nunca para frente. */
  duration: (minutes: number) => string;
  /** A data de hoje NO FUSO DA CLÍNICA, como "2026-08-31". O extrato agrupa
   *  por este dia; o cliente usava o dia do navegador e o card "hoje" zerava. */
  todayKey: () => string;
}

const FALLBACK: ClinicProfile = {
  profile: "br",
  locale: "pt-BR",
  currency: "BRL",
  unit_system: "metric",
  timezone: "UTC",
  name_key: "compliance.profile.br",
  responsible_label_key: "responsible.owner",
  patient_identifier_kinds: [],
  retention_years: 5,
  license_authority_label_key: "compliance.br.license_authority_label",
  subscription_status: "active",
  trial_ends_at: null,
};

const ClinicContext = createContext<ClinicContextValue | null>(null);

export function ClinicProvider({ children }: { children: ReactNode }) {
  const [profile, setProfile] = useState<ClinicProfile | null>(null);

  const { i18n } = useTranslation();

  useEffect(() => {
    let alive = true;
    void api
      .clinicProfile()
      .then((value) => {
        if (!alive) return;
        setProfile(value);
        // O idioma é da CLÍNICA, não do navegador.
        //
        // `navigator.language` fazia uma clínica brasileira num quiosque com
        // Chrome em inglês ver a interface em inglês, enquanto o servidor
        // gerava as cerimônias, o boletim e o prontuário em pt-BR, porque ele
        // usa `clinics.locale`. Metade do produto num idioma e metade no outro.
        // O ADR-0004 é explícito: o idioma é atributo da clínica.
        if (value.locale && i18n.language !== value.locale) {
          void i18n.changeLanguage(value.locale);
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [i18n]);

  return (
    <ClinicContext.Provider value={useFormatters(profile)}>{children}</ClinicContext.Provider>
  );
}

function useFormatters(profile: ClinicProfile | null): ClinicContextValue {
  const { i18n, t } = useTranslation();
  const language = i18n.language;
  const timezone = profile?.timezone ?? FALLBACK.timezone;
  const currency = profile?.currency ?? FALLBACK.currency;

  return useMemo(() => {
    const timeFmt = new Intl.DateTimeFormat(language, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: timezone,
    });
    const dateFmt = new Intl.DateTimeFormat(language, {
      day: "2-digit",
      month: "2-digit",
      timeZone: timezone,
    });
    const dayFmt = new Intl.DateTimeFormat(language, {
      weekday: "long",
      day: "2-digit",
      month: "long",
      timeZone: timezone,
    });
    const moneyFmt = new Intl.NumberFormat(language, { style: "currency", currency });
    // Chave do dia no fuso da clínica: "en-CA" dá ISO (2026-08-31) e é estável
    // entre navegadores. Comparar strings de dia é mais seguro que aritmética
    // de milissegundos, que erra na virada do horário de verão.
    const dayKeyFmt = new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: timezone,
    });
    const asDate = (value: string | Date) => (value instanceof Date ? value : new Date(value));

    return {
      profile,
      timezone,
      currency,
      time: (value) => timeFmt.format(asDate(value)),
      moment: (value) => {
        const when = asDate(value);
        const hoje = dayKeyFmt.format(when) === dayKeyFmt.format(new Date());
        // Uma dose de anteontem exibida só como "10:00" se confunde com a de
        // hoje: a pessoa vê duas linhas iguais e conclui que a baixa falhou.
        return hoje ? timeFmt.format(when) : `${dateFmt.format(when)} ${timeFmt.format(when)}`;
      },
      day: (value) => dayFmt.format(asDate(value)),
      money: (minor) => moneyFmt.format((minor ?? 0) / 100),
      // "," em pt-BR, "." em en-US. Um campo de preço mostrando "18.00" ao
      // lado de uma concentração "500,5" na mesma tela é a interface falando
      // duas línguas de número de uma vez.
      decimalSeparator: new Intl.NumberFormat(language).format(1.1).charAt(1),
      number: (value, maxFractionDigits = 3) => {
        const parsed = typeof value === "number" ? value : Number(value);
        if (value === null || value === undefined || Number.isNaN(parsed)) return "–";
        return new Intl.NumberFormat(language, { maximumFractionDigits: maxFractionDigits }).format(
          parsed,
        );
      },
      duration: (minutes) => {
        const total = Math.abs(Math.round(minutes));
        if (total < 60) return t("time.minutes", { n: total });
        const horas = Math.floor(total / 60);
        const resto = total % 60;
        return resto === 0
          ? t("time.hours", { n: horas })
          : t("time.hoursMinutes", { h: horas, m: resto });
      },
      todayKey: () => dayKeyFmt.format(new Date()),
    };
  }, [language, timezone, currency, profile, t]);
}

export function useClinic(): ClinicContextValue {
  const context = useContext(ClinicContext);
  if (!context) throw new Error("useClinic precisa do ClinicProvider");
  return context;
}
