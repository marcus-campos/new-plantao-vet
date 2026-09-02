import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import "../styles/combobox.css";

export interface ComboboxOption {
  value: string;
  label: string;
  /** Segunda linha: espécie, box, tutor, CRMV. O que desempata homônimos. */
  hint?: string;
  /** Texto extra que a busca considera sem aparecer na tela (microchip, CPF). */
  keywords?: string;
  disabled?: boolean;
}

/** Abaixo disto a caixa de busca é ruído: 4 opções se leem de uma vez. */
const SEARCH_THRESHOLD = 6;

function fold(text: string): string {
  // Sem acento e sem caixa: "Nina" acha "niná", "Josue" acha "Josué".
  return text
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

/**
 * Select pesquisável. Substitui `<select>` em toda lista que pode crescer:
 * paciente, box, profissional, item da tabela de preços.
 *
 * Em modo assíncrono (`onSearch`) quem filtra é o servidor. É assim que a
 * busca de paciente acha por microchip ou pelo documento do tutor sem baixar
 * a base inteira para o navegador.
 */
export function Combobox({
  value,
  onChange,
  options,
  placeholder,
  emptyLabel,
  disabled = false,
  required = false,
  onSearch,
  searchDebounceMs = 250,
  autoFocus = false,
  onCreate,
  createLabel,
  id,
}: {
  value: string;
  onChange: (value: string) => void;
  options: ComboboxOption[];
  placeholder?: string;
  /** Rótulo da opção vazia. Omitido = campo obrigatório, sem "nenhum". */
  emptyLabel?: string;
  disabled?: boolean;
  required?: boolean;
  onSearch?: (query: string) => void;
  searchDebounceMs?: number;
  autoFocus?: boolean;
  /** Cadastrar o que não existe SEM sair da lista. Recebe o que foi digitado:
   *  quem procurou "Thor" e não achou não deve redigitar "Thor". */
  onCreate?: (query: string) => void;
  createLabel?: (query: string) => string;
  id?: string;
}) {
  const { t } = useTranslation();
  const generatedId = useId();
  const fieldId = id ?? generatedId;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);

  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const listRef = useRef<HTMLUListElement | null>(null);

  const selected = options.find((option) => option.value === value) ?? null;

  const visible = useMemo(() => {
    // Servidor filtrando: mostrar o que ele mandou, sem filtrar de novo.
    if (onSearch || query.trim() === "") return options;
    const needle = fold(query.trim());
    return options.filter((option) =>
      fold(`${option.label} ${option.hint ?? ""} ${option.keywords ?? ""}`).includes(needle),
    );
  }, [options, query, onSearch]);

  const showSearch = onSearch != null || options.length >= SEARCH_THRESHOLD;

  // Busca no servidor com debounce: uma tecla não é uma requisição.
  useEffect(() => {
    if (!onSearch) return;
    const term = query.trim();
    const timer = setTimeout(() => onSearch(term), searchDebounceMs);
    return () => clearTimeout(timer);
  }, [query, onSearch, searchDebounceMs]);

  // Fecha ao clicar fora: sem isso a lista fica pendurada sobre a tela.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (open) setCursor(0);
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({
      block: "nearest",
    });
  }, [cursor, open]);

  const choose = useCallback(
    (option: ComboboxOption | null) => {
      onChange(option?.value ?? "");
      setOpen(false);
      setQuery("");
    },
    [onChange],
  );

  function openAndFocus() {
    if (disabled) return;
    setOpen(true);
    // O foco só existe depois que a lista entra no DOM.
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
      event.preventDefault();
      openAndFocus();
      return;
    }
    if (!open) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setCursor((current) => Math.min(current + 1, visible.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setCursor((current) => Math.max(current - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const option = visible[cursor];
      if (option && !option.disabled) choose(option);
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  }

  const label = selected?.label ?? emptyLabel ?? placeholder ?? "";

  return (
    <div className="combobox" ref={rootRef} onKeyDown={onKeyDown}>
      <button
        type="button"
        id={fieldId}
        className={selected ? "combobox-trigger" : "combobox-trigger combobox-empty"}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : openAndFocus())}
      >
        <span className="combobox-value">
          <span>{label}</span>
          {selected?.hint ? <span className="combobox-hint">{selected.hint}</span> : null}
        </span>
        <span aria-hidden="true" className="combobox-caret">
          ▾
        </span>
      </button>

      {/* Espelha o valor para a validação nativa do formulário continuar valendo. */}
      {required ? (
        <input
          className="combobox-mirror"
          tabIndex={-1}
          value={value}
          required
          onChange={() => undefined}
          aria-hidden="true"
        />
      ) : null}

      {open ? (
        <div className="combobox-pop">
          {showSearch ? (
            <input
              ref={inputRef}
              className="combobox-search"
              value={query}
              autoFocus={autoFocus || undefined}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={placeholder ?? t("combobox.search")}
              aria-label={placeholder ?? t("combobox.search")}
            />
          ) : null}

          <ul className="combobox-list" role="listbox" ref={listRef}>
            {emptyLabel ? (
              <li>
                <button
                  type="button"
                  role="option"
                  aria-selected={value === ""}
                  className="combobox-option combobox-option-empty"
                  onClick={() => choose(null)}
                >
                  {emptyLabel}
                </button>
              </li>
            ) : null}

            {visible.map((option, index) => (
              <li key={option.value}>
                <button
                  type="button"
                  role="option"
                  aria-selected={option.value === value}
                  data-active={index === cursor}
                  disabled={option.disabled}
                  className={
                    option.value === value ? "combobox-option combobox-on" : "combobox-option"
                  }
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => choose(option)}
                >
                  <span>{option.label}</span>
                  {option.hint ? <span className="combobox-hint">{option.hint}</span> : null}
                </button>
              </li>
            ))}

            {visible.length === 0 ? (
              <li className="combobox-none">{t("combobox.noResults")}</li>
            ) : null}
          </ul>

          {onCreate ? (
            <button
              type="button"
              className="combobox-create"
              onClick={() => {
                const typed = query.trim();
                setOpen(false);
                setQuery("");
                onCreate(typed);
              }}
            >
              <span aria-hidden="true">+</span>
              <span>
                {createLabel ? createLabel(query.trim()) : t("combobox.create")}
              </span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
