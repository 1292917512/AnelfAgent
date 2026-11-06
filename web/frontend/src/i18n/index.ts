import i18n, { type BackendModule, type ResourceLanguage } from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { entityLocaleNamespaces, loadEntityLocale } from "@/lib/entity-plugin-locales";
import zhCommon from "./locales/zh/common.json";
import enCommon from "./locales/en/common.json";

const modules = import.meta.glob<{ default: ResourceLanguage }>("./locales/*/*.json");
const namespaces = [...new Set([...Object.keys(modules).map((path) => path.split("/").pop()!.slice(0, -5)), ...entityLocaleNamespaces])];

/** Vite 资源经 i18next 后端协议加载，语言切换沿用其等待、回退与并发协调。 */
const backend: BackendModule = {
  type: "backend",
  init() {},
  read(language, namespace, callback) {
    const load = async () => {
      const core = await modules[`./locales/${language}/${namespace}.json`]?.();
      if (core) i18n.addResourceBundle(language, namespace, core.default, true, true);
      await loadEntityLocale(language, namespace);
      return i18n.getResourceBundle(language, namespace) ?? {};
    };
    void load().then((bundle) => callback(null, bundle), (error: unknown) => callback(error instanceof Error ? error : new Error(String(error)), false));
  },
};

export const i18nReady = new Promise<void>((resolve, reject) => {
  void i18n.use(LanguageDetector).use(backend).use(initReactI18next).init({
    resources: { zh: { common: zhCommon }, en: { common: enCommon } },
    partialBundledLanguages: true,
    fallbackLng: "zh", supportedLngs: ["zh", "en"], load: "languageOnly",
    defaultNS: "common", ns: namespaces,
    interpolation: { escapeValue: false },
    detection: { order: ["localStorage"], caches: ["localStorage"] },
  }, (error: unknown) => { if (error) reject(error); else resolve(); });
});

export default i18n;
