import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { Sun, Moon, Languages, Menu, Search, ChevronRight, Rows3 } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { findRoute } from "@/lib/navigation";
import { Button } from "@/components/ui/Button";
import { ConnectionStatus } from "./ConnectionStatus";

export function Header() {
  const { t, i18n } = useTranslation(["nav", "palette"]);
  const theme = useAppStore((state) => state.theme);
  const toggleTheme = useAppStore((state) => state.toggleTheme);
  const setMobileMenuOpen = useAppStore((state) => state.setMobileMenuOpen);
  const setPaletteOpen = useAppStore((state) => state.setPaletteOpen);
  const density = useAppStore((state) => state.density);
  const toggleDensity = useAppStore((state) => state.toggleDensity);
  const location = useLocation();
  const route = findRoute(location.pathname);
  const modKey = /mac/i.test(navigator.platform) ? "⌘ K" : "Ctrl K";
  return (
    <header className="app-header flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-3 md:px-6">
      <div className="flex min-w-0 items-center gap-2">
        <Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobileMenuOpen(true)} aria-label={t("menu", { ns: "palette" })}><Menu size={20} /></Button>
        {location.pathname !== "/" && <><Link to="/" className="hidden text-xs text-muted hover:text-accent sm:block">{t("chat")}</Link><ChevronRight size={13} className="hidden text-muted sm:block" /></>}
        {route && <><route.icon size={16} className="shrink-0 text-muted" /><span className="truncate text-sm font-medium text-heading">{t(route.label, { defaultValue: route.label })}</span></>}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <ConnectionStatus />
        <button type="button" onClick={() => setPaletteOpen(true)} aria-label={t("label", { ns: "palette" })}
          className="ml-1 flex h-8 items-center gap-2 rounded-lg border border-border bg-card px-2.5 text-xs text-muted hover:border-border-hover hover:text-heading">
          <Search size={14} /><span className="hidden lg:inline">{t("quickSearch")}</span><kbd className="hidden text-[10px] sm:inline">{modKey}</kbd>
        </button>
        <Button variant="ghost" size="icon" className="hidden sm:inline-flex" aria-label={t(density === "comfortable" ? "compactDensity" : "comfortableDensity")}
          title={t(density === "comfortable" ? "compactDensity" : "comfortableDensity")} onClick={toggleDensity}><Rows3 size={16} /></Button>
        <Button variant="ghost" size="icon" aria-label={i18n.resolvedLanguage === "zh" ? "Switch to English" : "切换为中文"}
          onClick={() => void i18n.changeLanguage(i18n.resolvedLanguage === "zh" ? "en" : "zh")}><Languages size={16} /></Button>
        <Button variant="ghost" size="icon" aria-label={t(theme === "dark" ? "action_theme_light" : "action_theme_dark", { ns: "palette" })} onClick={toggleTheme}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </Button>
      </div>
    </header>
  );
}
