import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

// 自动加载所有 locales/<lang>/<ns>.json，命名规则决定 namespace 名
// 新增 namespace 只需新建文件，无需改动本模块
const modules = import.meta.glob("./locales/*/*.json", { eager: true });

interface LocaleModule {
  default: Record<string, unknown>;
}

const resources: Record<string, Record<string, Record<string, unknown>>> = {};
const namespaces = new Set<string>();

for (const [path, mod] of Object.entries(modules)) {
  const m = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.json$/);
  if (!m) continue;
  const lang = m[1];
  const ns = m[2];
  if (!lang || !ns) continue;
  (resources[lang] ??= {})[ns] = (mod as LocaleModule).default;
  namespaces.add(ns);
}

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: resources as never,
    fallbackLng: "zh",
    supportedLngs: ["zh", "en"],
    defaultNS: "common",
    ns: Array.from(namespaces),
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
  });

export default i18n;
