import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zhCN from "./locales/zh-CN.json";
import enUS from "./locales/en-US.json";

export type AppLocale = "zh-CN" | "en-US";

const STORAGE_KEY = "eaos:locale";

const SUPPORTED: AppLocale[] = ["zh-CN", "en-US"];
const DEFAULT_LOCALE: AppLocale = "zh-CN";

/** Read the user's stored locale (no server round-trip). */
export function getStoredLocale(): AppLocale {
  if (typeof localStorage === "undefined") return DEFAULT_LOCALE;
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && SUPPORTED.includes(stored as AppLocale)) {
    return stored as AppLocale;
  }
  // First-visit heuristic: match browser language.
  const navLang = typeof navigator !== "undefined" ? navigator.language : "";
  if (navLang.startsWith("en")) return "en-US";
  return DEFAULT_LOCALE;
}

/** Persist locale choice and apply it immediately. */
export function setLocale(locale: AppLocale): void {
  localStorage.setItem(STORAGE_KEY, locale);
  void i18n.changeLanguage(locale);
  document.documentElement.setAttribute("lang", locale);
}

/** Initialise i18n — call once at app boot before React renders. */
export function initI18n(): typeof i18n {
  const locale = getStoredLocale();
  if (!i18n.isInitialized) {
    i18n.use(initReactI18next).init({
      resources: {
        "zh-CN": { translation: zhCN },
        "en-US": { translation: enUS },
      },
      lng: locale,
      fallbackLng: DEFAULT_LOCALE,
      interpolation: { escapeValue: false },
      react: { useSuspense: false },
    });
  }
  document.documentElement.setAttribute("lang", locale);
  return i18n;
}

export { i18n };
