/**
 * 插件 i18n 注册 — 模块前端（channels/<id>/frontend、entities/<name>/panel）
 * 自带 locales/{zh,en}.json，模块加载时经本函数自注册。
 *
 * 命名空间约定：频道插件用 `channel-<id>`；实体面板沿用原命名空间名（如 "ssh"）。
 * 覆盖语义：deep 合并 + overwrite，插件包与核心 locale 同名键以插件为准。
 */
import i18n from "@/i18n";

export interface PluginLocales {
  zh: Record<string, unknown>;
  en: Record<string, unknown>;
}

export function registerPluginI18n(ns: string, locales: PluginLocales): void {
  i18n.addResourceBundle("zh", ns, locales.zh, true, true);
  i18n.addResourceBundle("en", ns, locales.en, true, true);
}
