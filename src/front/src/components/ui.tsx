import { useCallback } from "react";
import type { CSSProperties, ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { DisplayState } from "../api/types";
import { ApiError } from "../api/client";

const STATE_COLORS: Record<string, { fg: string; bg: string; edge: string }> = {
  on_time: { fg: "var(--ok)", bg: "var(--ok-bg)", edge: "var(--ok-edge)" },
  due: { fg: "var(--warn)", bg: "var(--warn-bg)", edge: "var(--warn-edge)" },
  overdue: { fg: "var(--late)", bg: "var(--late-bg)", edge: "var(--late-edge)" },
  done: { fg: "var(--ok)", bg: "var(--ok-bg)", edge: "var(--ok-edge)" },
  partial: { fg: "var(--warn)", bg: "var(--warn-bg)", edge: "var(--warn-edge)" },
  not_done: { fg: "var(--ink-3)", bg: "var(--line-soft)", edge: "var(--line)" },
  cancelled: { fg: "var(--ink-muted)", bg: "var(--line-soft)", edge: "var(--line)" },
};

export function stateColors(state: string) {
  return STATE_COLORS[state] ?? STATE_COLORS.cancelled;
}

export function StatePill({ state }: { state: DisplayState | string }) {
  const { t } = useTranslation();
  const colors = stateColors(state);
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: 13,
        fontWeight: 600,
        color: colors.fg,
        background: colors.bg,
        borderRadius: 999,
        padding: "6px 14px",
        whiteSpace: "nowrap",
      }}
    >
      {t(`state.${state}`)}
    </span>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: CSSProperties }) {
  return (
    <div
      style={{
        background: "var(--surface)",
        border: "1px solid var(--line)",
        borderRadius: "var(--radius)",
        padding: 18,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  style,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  type?: "button" | "submit";
  style?: CSSProperties;
}) {
  const palette =
    variant === "primary"
      ? { background: "var(--primary)", color: "#fff", border: "1px solid var(--primary)" }
      : variant === "danger"
        ? { background: "var(--late-bg)", color: "var(--late)", border: "1px solid var(--late-edge)" }
        : { background: "var(--surface)", color: "var(--ink-2)", border: "1px solid var(--line)" };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      style={{
        ...palette,
        borderRadius: 8,
        padding: "11px 18px",
        fontWeight: 600,
        fontSize: 15,
        opacity: disabled ? 0.55 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
        ...style,
      }}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <span
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: "var(--ink-2)",
          letterSpacing: "0.04em",
          textTransform: "uppercase",
        }}
      >
        {label}
      </span>
      {children}
    </label>
  );
}

/** "0,15" é como se escreve número em pt-BR, e a vírgula ia crua para a API,
 *  que só entende ponto: o cálculo de dose morria e o campo parecia quebrado.
 *  Vive aqui porque todo campo numérico do app tem o mesmo problema. */
export function decimal(value: string): string {
  return value.replace(",", ".").trim();
}

/** O mesmo número no caminho de volta: a API devolve "0.15" e o formulário
 *  precisa mostrar "0,15" para quem digita.
 *
 *  Os zeros à direita caem. A coluna do banco é `Numeric(12, 4)`, então uma
 *  concentração de 500 mg/ml voltava como "500.0000" e o campo mostrava
 *  "500,0000": quatro casas que ninguém digitou, em quatro campos seguidos,
 *  competindo com os números que importam. Vazio continua vazio. */
export function decimalField(
  value: string | null | undefined,
  separator: string = ",",
): string {
  if (value === null || value === undefined || value === "") return "";
  const texto = String(value);
  const limpo = texto.includes(".") ? texto.replace(/\.?0+$/, "") : texto;
  return (limpo || "0").replace(".", separator);
}

export function telefone(value: string): string {
  return value.replace(/\D/g, "").slice(0, 11);
}

export function telefoneField(value: string | null | undefined): string {
  const digitos = telefone(String(value ?? ""));
  if (digitos.length <= 2) return digitos;
  if (digitos.length <= 6) return `(${digitos.slice(0, 2)}) ${digitos.slice(2)}`;
  if (digitos.length <= 10) {
    return `(${digitos.slice(0, 2)}) ${digitos.slice(2, 6)}-${digitos.slice(6)}`;
  }
  return `(${digitos.slice(0, 2)}) ${digitos.slice(2, 7)}-${digitos.slice(7)}`;
}

export const inputStyle: CSSProperties = {
  border: "1px solid var(--line)",
  borderRadius: 8,
  padding: "11px 14px",
  fontSize: 15,
  background: "var(--surface)",
  width: "100%",
};

/** Traduz o código de erro da API. A API nunca manda prosa (ADR-0004).
 *
 *  A função PRECISA ser estável entre renders: as telas a colocam no array de
 *  dependências de `useEffect`. Sem o useCallback, cada render cria uma função
 *  nova → o efeito dispara → muda estado → novo render → laço infinito de
 *  requisições. */
export function useApiErrorMessage() {
  const { t } = useTranslation();
  return useCallback(
    (error: unknown): string => {
      if (error instanceof ApiError) {
        return t(`error.${error.code}`, {
          ...error.params,
          defaultValue: t("error.unknown_error"),
        });
      }
      return t("error.unknown_error");
    },
    [t],
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      style={{
        background: "var(--late-bg)",
        border: "1px solid var(--late-edge)",
        color: "var(--late)",
        borderRadius: 8,
        padding: "11px 14px",
        fontSize: 14,
        fontWeight: 500,
      }}
    >
      {message}
    </div>
  );
}


/** Rótulo do horário de uma tarefa, com a DATA quando não é de hoje.
 *
 *  Dose vencida nunca sai da fila, e uma dose de anteontem exibida só como
 *  "10:00" se confunde com a de hoje: a pessoa vê duas linhas "10:00", uma
 *  feita e outra pendente, e conclui que a baixa não funcionou. */
export function scheduleLabel(
  iso: string,
  timeFmt: Intl.DateTimeFormat,
  locale: string,
  now: Date = new Date(),
): string {
  const when = new Date(iso);
  const time = timeFmt.format(when);
  if (when.toDateString() === now.toDateString()) return time;
  const day = new Intl.DateTimeFormat(locale, { day: "2-digit", month: "2-digit" }).format(when);
  return `${day} ${time}`;
}

/* ------------------------------------------------------------------------
 * Chrome de página.
 *
 * Cada uma das 18 telas desenhava o próprio cabeçalho, o próprio vazio e o
 * próprio "Carregando…", com estilo inline. O resultado é o que o produto
 * parece: várias equipes diferentes. Estes componentes são pequenos e
 * explícitos de propósito: o problema não era falta de abstração, era falta
 * de um padrão.
 * ---------------------------------------------------------------------- */

export function Page({
  title,
  eyebrow,
  subtitle,
  actions,
  children,
}: {
  title: string;
  eyebrow?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <header className="page-head">
        <div className="page-head-text">
          {eyebrow ? <span className="eyebrow">{eyebrow}</span> : null}
          <h1 className="page-title">{title}</h1>
          {subtitle ? <p className="page-subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="page-actions">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}

export function Section({
  title,
  hint,
  actions,
  children,
}: {
  title?: string;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="section">
      {title || actions ? (
        <div className="section-head">
          <div>
            {title ? <h2 className="section-title">{title}</h2> : null}
            {hint ? <p className="section-hint">{hint}</p> : null}
          </div>
          {actions ? <div className="section-actions">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

/** Estado vazio que diz o que fazer, não só que não há nada.
 *
 *  "Nenhum paciente internado agora." é uma constatação; o vazio útil oferece
 *  o próximo passo. */
export function EmptyState({
  title,
  hint,
  action,
  tone = "neutral",
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  tone?: "neutral" | "good";
}) {
  return (
    <div className={`empty empty-${tone}`}>
      <strong className="empty-title">{title}</strong>
      {hint ? <p className="empty-hint">{hint}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

/** Esqueleto no formato do conteúdo que vem.
 *
 *  Um spinner de tela cheia joga fora o layout que a pessoa já conhecia e
 *  esconde o fato de que os dados anteriores continuam válidos. */
export function Skeleton({ rows = 3, height = 64 }: { rows?: number; height?: number }) {
  return (
    <div className="skeleton-stack" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="skeleton" style={{ height }} />
      ))}
    </div>
  );
}

/** Erro com saída. Um banner sem ação deixa a pessoa parada. */
export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="error-state" role="alert">
      <strong>{message}</strong>
      {onRetry ? (
        <Button variant="secondary" onClick={onRetry}>
          {t("common.retry")}
        </Button>
      ) : null}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warn" | "late" | "accent";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

/** Um número que ajuda a decidir. Estava copiado em três telas. */
export function Stat({
  value,
  label,
  tone = "ink",
  hint,
}: {
  value: number | string;
  label: string;
  tone?: "ink" | "good" | "warn" | "late" | "accent";
  hint?: ReactNode;
}) {
  return (
    <div className="stat">
      <div className={`stat-value tabular stat-${tone}`}>{value}</div>
      <div className="stat-label">{label}</div>
      {hint ? <div className="stat-hint">{hint}</div> : null}
    </div>
  );
}
