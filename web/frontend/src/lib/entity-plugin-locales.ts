/**
 * 实体面板 locale 的启动时注册 — 面板组件懒加载，但工具页分组名 /
 * 配置中心分组名等全局词汇必须在首帧前就绪。
 *
 * 由 i18n 初始化完成后调用一次（src/i18n/index.ts），import.meta.glob eager
 * 收集 panels/<name>/locales/{zh,en}.json（JSON 体积极小，静态打入主 chunk；
 * 无面板的实体也可只放 locales 目录自持翻译）：
 *
 * 1. 整包（剥除保留键 _registry）注册进实体命名空间 <name>——面板打开前
 *    文案已就绪，panel.tsx 无需再自行 registerPluginI18n；
 * 2. _registry 声明需合入核心命名空间的全局词汇（显式键映射，实体目录名
 *    与分组 key 不必相同，一个实体可拥有多个分组）：
 *    - groups: { "<group key>": "展示名" } → tools 命名空间 groups.*
 *      （工具页分组名）；
 *    - configSections: { "entity/<key>": "展示名" } → config 命名空间
 *      sections.*（配置中心分组名）；
 *    实体翻译自持于模块目录，热拔出零残留，核心 locale 不写实体条目。
 */
import { registerPluginI18n } from "./plugin-i18n";

/** locale 文件保留键：需合入核心命名空间的全局词汇（显式键 → 展示名） */
interface RegistryMeta {
  /** 工具页分组名映射（group key → 展示名） */
  groups?: Record<string, string>;
  /** 配置中心分组名映射（section key → 展示名，如 "entity/os"） */
  configSections?: Record<string, string>;
}

type LocaleBundle = Record<string, unknown> & { _registry?: RegistryMeta };
type Lang = "zh" | "en";

const localeModules = import.meta.glob<{ default: LocaleBundle }>(
  "../pages/entities/panels/*/locales/*.json",
  { eager: true },
);

interface Collected {
  bundles: Partial<Record<Lang, Record<string, unknown>>>;
  metas: Partial<Record<Lang, RegistryMeta>>;
}

/** 注册全部实体面板的 locale（i18n 初始化完成后调用一次） */
export function registerEntityPluginLocales(): void {
  const collected = new Map<string, Collected>();
  for (const [path, mod] of Object.entries(localeModules)) {
    const match = path.match(/\/panels\/([^/]+)\/locales\/(zh|en)\.json$/);
    const name = match?.[1];
    const lang = match?.[2] as Lang | undefined;
    if (!name || !lang) continue;
    const entry = collected.get(name) ?? { bundles: {}, metas: {} };
    const { _registry, ...bundle } = mod.default;
    entry.bundles[lang] = bundle;
    entry.metas[lang] = _registry ?? {};
    collected.set(name, entry);
  }

  for (const [name, { bundles, metas }] of collected) {
    registerPluginI18n(name, { zh: bundles.zh ?? {}, en: bundles.en ?? {} });

    const groupNames: Partial<Record<Lang, Record<string, unknown>>> = {};
    const sections: Partial<Record<Lang, Record<string, unknown>>> = {};
    for (const lang of ["zh", "en"] as const) {
      const meta = metas[lang] ?? {};
      if (meta.groups && Object.keys(meta.groups).length > 0) {
        groupNames[lang] = { groups: meta.groups };
      }
      if (meta.configSections && Object.keys(meta.configSections).length > 0) {
        sections[lang] = { sections: meta.configSections };
      }
    }
    if (Object.keys(groupNames).length > 0) {
      registerPluginI18n("tools", {
        zh: groupNames.zh ?? {},
        en: groupNames.en ?? {},
      });
    }
    if (Object.keys(sections).length > 0) {
      registerPluginI18n("config", {
        zh: sections.zh ?? {},
        en: sections.en ?? {},
      });
    }
  }
}
