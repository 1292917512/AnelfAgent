import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { Sun, Moon, Languages, Menu, Search } from "lucide-react";

export function Header() {
  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const setMobileMenuOpen = useAppStore((s) => s.setMobileMenuOpen);
  const branding = useAppStore((s) => s.branding);
  const setPaletteOpen = useAppStore((s) => s.setPaletteOpen);
  const { t, i18n } = useTranslation("palette");

  // ⌘K 提示按平台显示（Mac 用 ⌘，其余 Ctrl）
  const modKey =
    typeof navigator !== "undefined" && /mac/i.test(navigator.platform) ? "\u2318K" : "Ctrl K";

  const toggleLang = () => i18n.changeLanguage(i18n.language === "zh" ? "en" : "zh");

  return (
    <header className="flex items-center justify-between gap-1 h-14 px-3 md:px-6 border-b border-border bg-panel shrink-0">
      {/* 移动端：汉堡菜单 + 品牌名 */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setMobileMenuOpen(true)}
          className="md:hidden p-2 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
          aria-label="菜单"
        >
          <Menu size={20} />
        </button>
        <span className="md:hidden text-sm font-semibold text-heading">
          {branding.title}
        </span>
      </div>

      <div className="flex items-center gap-1">
        {/* 命令面板入口：⌘K / Ctrl+K */}
        <button
          onClick={() => setPaletteOpen(true)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium text-muted hover:text-foreground hover:bg-hover transition-colors"
          title={t("label")}
          aria-label={t("label")}
        >
          <Search size={16} />
          <kbd className="hidden md:inline rounded border border-border bg-elevated px-1 py-0.5 text-[10px] font-mono text-muted">
            {modKey}
          </kbd>
        </button>
        <button
          onClick={toggleLang}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium text-muted hover:text-foreground hover:bg-hover transition-colors"
          title={i18n.language === "zh" ? "Switch to English" : "切换为中文"}
        >
          <Languages size={16} />
          {i18n.language === "zh" ? "EN" : "中"}
        </button>
        <button
          onClick={toggleTheme}
          className="p-2 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
        </button>
      </div>
    </header>
  );
}
