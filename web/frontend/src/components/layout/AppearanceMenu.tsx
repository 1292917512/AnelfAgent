import * as Popover from "@radix-ui/react-popover";
import { Languages, Moon, Rows3, SlidersHorizontal, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useAppStore } from "@/stores/app-store";

export function AppearanceMenu() {
  const { t, i18n } = useTranslation(["nav", "palette"]);
  const theme = useAppStore((s) => s.theme);
  const density = useAppStore((s) => s.density);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const toggleDensity = useAppStore((s) => s.toggleDensity);
  return <Popover.Root>
    <Popover.Trigger asChild><Button variant="ghost" size="icon" aria-label={t("appearance")}><SlidersHorizontal size={17} /></Button></Popover.Trigger>
    <Popover.Portal><Popover.Content align="end" sideOffset={10} className="appearance-menu z-[110] w-64 rounded-2xl border border-border bg-card p-2 shadow-lg">
      <p className="px-3 py-2 text-xs font-semibold text-heading">{t("appearance")}</p>
      <button type="button" onClick={toggleTheme} className="menu-action">{theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}{t(theme === "dark" ? "action_theme_light" : "action_theme_dark", { ns: "palette" })}</button>
      <button type="button" onClick={toggleDensity} className="menu-action"><Rows3 size={17} />{t(density === "comfortable" ? "compactDensity" : "comfortableDensity")}</button>
      <button type="button" onClick={() => void i18n.changeLanguage(i18n.resolvedLanguage === "zh" ? "en" : "zh")} className="menu-action"><Languages size={17} />{i18n.resolvedLanguage === "zh" ? "English" : "简体中文"}</button>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}
