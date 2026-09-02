import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { ApiError } from "../api/client";
import { PinDialog } from "./PinDialog";
import { useApiErrorMessage } from "./ui";

/** Mutação nas telas de gestão: no modo estação a API devolve
 *  `operator_required` e a ação só é gravada depois do PIN. O hook guarda a
 *  ação pendente, abre o teclado de PIN e refaz a chamada. Quem chama não
 *  precisa saber em que modo a sessão está. */
export function usePinRetry() {
  const describeError = useApiErrorMessage();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<{ action: () => Promise<void> } | null>(null);

  const run = useCallback(
    async (action: () => Promise<void>) => {
      setBusy(true);
      setError(null);
      try {
        await action();
      } catch (err) {
        if (err instanceof ApiError && err.code === "operator_required") {
          setPending({ action });
        } else {
          setError(describeError(err));
        }
      } finally {
        setBusy(false);
      }
    },
    [describeError],
  );

  const dialog = pending ? (
    <PinDialog
      onDone={() => {
        const action = pending.action;
        setPending(null);
        void run(action);
      }}
      onCancel={() => setPending(null)}
    />
  ) : null;

  return { run, dialog, error, setError, busy, describeError };
}

export function AdminHeader({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
}) {
  return (
    <div className="admin-head">
      <div>
        <h1 className="admin-title">{title}</h1>
        {subtitle ? <p className="admin-subtitle">{subtitle}</p> : null}
      </div>
      {children ? <div className="admin-actions">{children}</div> : null}
    </div>
  );
}

/** Diálogo das telas de gestão. Fecha no Esc e no clique fora; o mesmo
 *  contêiner visual do PinDialog, para não haver dois jeitos de abrir modal. */
export function AdminModal({
  title,
  onClose,
  children,
  wide = false,
  xwide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** Duas colunas de trabalho lado a lado. O item de preço com posologia não
   *  cabe numa coluna: empilhado, o "Salvar" do item caía no meio da tela com
   *  outra seção inteira abaixo dele, e o que era o fim do trabalho passava a
   *  parecer um passo do meio. */
  xwide?: boolean;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={
          xwide
            ? "modal-card admin-modal-xwide"
            : wide
              ? "modal-card admin-modal-wide"
              : "modal-card"
        }
      >
        <h2 style={{ fontSize: 19 }}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function AdminNote({
  tone = "warn",
  children,
}: {
  tone?: "warn" | "neutral" | "danger";
  children: ReactNode;
}) {
  const className =
    tone === "neutral"
      ? "admin-note admin-note-neutral"
      : tone === "danger"
        ? "admin-note admin-note-danger"
        : "admin-note";
  return <div className={className}>{children}</div>;
}

export function CheckRow({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <label style={{ display: "flex", gap: 12, alignItems: "flex-start", cursor: "pointer" }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        style={{ minHeight: 0, width: 20, height: 20, marginTop: 2 }}
      />
      <span>
        <strong style={{ fontSize: 14.5 }}>{label}</strong>
        {hint ? (
          <span style={{ display: "block", fontSize: 13, color: "var(--ink-3)" }}>{hint}</span>
        ) : null}
      </span>
    </label>
  );
}

/** "CRMV-SP 12345", ou nada quando a pessoa não tem registro.
 *
 *  Lido pela lista de equipe e pela escala: um turno com veterinário
 *  responsável só é evidência de conformidade se disser nome E registro. */
export function license(row: { license_number: string | null; license_authority: string | null }) {
  if (!row.license_number) return null;
  return row.license_authority
    ? `${row.license_authority} ${row.license_number}`
    : row.license_number;
}

/** Iniciais para o avatar da lista de equipe. */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  const first = parts[0][0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1][0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Dinheiro chega em unidade menor (centavos). ADR-0004. */
export function useMoneyFormat(currency: string) {
  const { i18n } = useTranslation();
  return useCallback(
    (minor: number) =>
      new Intl.NumberFormat(i18n.language, { style: "currency", currency }).format(minor / 100),
    [i18n.language, currency],
  );
}
