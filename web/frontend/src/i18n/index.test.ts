import { expect, it } from "vitest";
import i18n, { i18nReady } from "./index";
import { panelLocales } from "@/generated/entity-panel-locales";

it("loads core and module translations before language changes resolve, including registry names", async () => {
  await i18nReady;
  for (const language of ["en", "zh"]) {
    await i18n.changeLanguage(language);
    expect(i18n.resolvedLanguage).toBe(language);
    expect(i18n.t("tabs.search", { ns: "chat" })).not.toBe("tabs.search");
    for (const entry of panelLocales.filter((item) => item.lang === language)) {
      expect(i18n.hasResourceBundle(language, entry.name)).toBe(true);
      const { _registry: _metadata, ...bundle } = (await entry.load()).default;
      expect(i18n.getResourceBundle(language, entry.name)).toMatchObject(bundle);
      for (const [name, value] of Object.entries(entry.registry.groups ?? {})) {
        expect(i18n.t(`groups.${name}`, { ns: "tools" })).toBe(value);
      }
      for (const [name, value] of Object.entries(entry.registry.configSections ?? {})) {
        expect(i18n.t(`sections.${name}`, { ns: "config" })).toBe(value);
      }
    }
  }
});
