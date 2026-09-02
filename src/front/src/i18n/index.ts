import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import ptBR from "./pt-BR.json";

/** pt-BR é o idioma-fonte; en existe desde a v1 (ADR-0004). */
export const SOURCE_LOCALE = "pt-BR";

/** Catálogos por tela vivem em src/i18n/extra/<nome>.<locale>.json e entram aqui
 *  automaticamente: assim cada tela cresce sem disputar um arquivo só. */
const extras = import.meta.glob<Record<string, string>>("./extra/*.json", {
  eager: true,
  import: "default",
});

function merge(locale: string, base: Record<string, string>): Record<string, string> {
  const merged = { ...base };
  for (const [path, catalog] of Object.entries(extras)) {
    if (path.endsWith(`.${locale}.json`)) Object.assign(merged, catalog);
  }
  return merged;
}

void i18n.use(initReactI18next).init({
  resources: {
    "pt-BR": { translation: merge("pt-BR", ptBR) },
    en: { translation: merge("en", en) },
  },
  // Palpite inicial só para o login, que acontece antes de haver clínica.
  // Assim que o perfil chega, `ClinicProvider` troca para `clinics.locale`.
  lng: navigator.language.startsWith("pt") ? "pt-BR" : "en",
  fallbackLng: SOURCE_LOCALE,
  // Catálogo plano: "patient.admittedFor" É a chave, não um caminho. Com o
  // separador ligado o i18next procurava `patient` → `admittedFor` e, no
  // plural, devolvia o literal da chave na tela.
  keySeparator: false,
  nsSeparator: false,
  interpolation: { escapeValue: false },
});

export default i18n;
