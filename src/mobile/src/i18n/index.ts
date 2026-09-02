import * as Localization from "expo-localization";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./en.json";
import ptBR from "./pt-BR.json";

const deviceLanguage = Localization.getLocales()[0]?.languageCode ?? "pt";

void i18n.use(initReactI18next).init({
  resources: { "pt-BR": { translation: ptBR }, en: { translation: en } },
  lng: deviceLanguage.startsWith("pt") ? "pt-BR" : "en",
  fallbackLng: "pt-BR",
  interpolation: { escapeValue: false },
});

export default i18n;
