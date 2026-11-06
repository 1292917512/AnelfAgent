import i18n from "i18next";
import { panelLocales } from "@/generated/entity-panel-locales";

export const entityLocaleNamespaces = [...new Set(panelLocales.map((entry) => entry.name))];

/** 模块文案由所属目录加载，注册名称合入核心命名空间；保持原有深合并与覆盖语义。 */
export async function loadEntityLocale(language: string, namespace: string): Promise<void> {
  const entry = panelLocales.find((item) => item.lang === language && item.name === namespace);
  if (entry) {
    const { _registry: _metadata, ...bundle } = (await entry.load()).default;
    i18n.addResourceBundle(language, namespace, bundle, true, true);
  }
  if (namespace !== "tools" && namespace !== "config") return;
  for (const item of panelLocales) {
    if (item.lang !== language) continue;
    const names = namespace === "tools" ? item.registry.groups : item.registry.configSections;
    if (names) i18n.addResourceBundle(language, namespace,
      { [namespace === "tools" ? "groups" : "sections"]: names }, true, true);
  }
}
