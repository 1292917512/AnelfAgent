import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import { Sun, Moon, Languages } from "lucide-react";

export function Header() {
  const { theme, toggleTheme } = useAppStore();
  const { i18n } = useTranslation();

  const toggleLang = () => i18n.changeLanguage(i18n.language === "zh" ? "en" : "zh");

  return (
    <header className="flex items-center justify-end gap-1 h-14 px-6 border-b border-[var(--border)] bg-[var(--panel)]">
      <button
        onClick={toggleLang}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-[var(--radius-md)] text-xs font-medium text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors"
        title={i18n.language === "zh" ? "Switch to English" : "切换为中文"}
      >
        <Languages size={16} />
        {i18n.language === "zh" ? "EN" : "中"}
      </button>
      <button
        onClick={toggleTheme}
        className="p-2 rounded-[var(--radius-md)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors"
      >
        {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
      </button>
    </header>
  );
}
