import { Command } from "cmdk";
import { useTranslation } from "react-i18next";
import { Languages, LogOut, Moon, PanelLeft, Search, Sun } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useAuthStore } from "@/stores/auth-store";
import { groupCls, itemCls } from "./paletteStyles";

/** 命令面板的快捷操作分组（主题 / 语言 / 侧栏 / 搜索 / 登出） */
export function PaletteActions({
  onClose,
  onOpenSearchPanel,
}: {
  onClose: () => void;
  onOpenSearchPanel: (q: string) => void;
}) {
  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const { t, i18n } = useTranslation("palette");
  return (
    <Command.Group heading={t("group_actions")} className={groupCls}>
      <Command.Item
        value={theme === "dark" ? t("action_theme_light") : t("action_theme_dark")}
        className={itemCls}
        onSelect={() => {
          toggleTheme();
          onClose();
        }}
      >
        {theme === "dark" ? (
          <Sun size={15} className="shrink-0 text-muted" />
        ) : (
          <Moon size={15} className="shrink-0 text-muted" />
        )}
        <span>
          {theme === "dark" ? t("action_theme_light") : t("action_theme_dark")}
        </span>
      </Command.Item>
      <Command.Item
        value={t("action_lang")}
        className={itemCls}
        onSelect={() => {
          i18n.changeLanguage(i18n.language === "zh" ? "en" : "zh");
          onClose();
        }}
      >
        <Languages size={15} className="shrink-0 text-muted" />
        <span>
          {i18n.language === "zh" ? "Switch to English" : "切换为中文"}
        </span>
      </Command.Item>
      <Command.Item
        value={t("action_sidebar")}
        className={itemCls}
        onSelect={() => {
          toggleSidebar();
          onClose();
        }}
      >
        <PanelLeft size={15} className="shrink-0 text-muted" />
        <span>{t("action_sidebar")}</span>
      </Command.Item>
      <Command.Item
        value={t("action_search")}
        className={itemCls}
        onSelect={() => onOpenSearchPanel("")}
      >
        <Search size={15} className="shrink-0 text-muted" />
        <span>{t("action_search")}</span>
      </Command.Item>
      <Command.Item
        value={t("action_logout")}
        className={itemCls}
        onSelect={() => {
          onClose();
          void useAuthStore.getState().logout();
        }}
      >
        <LogOut size={15} className="shrink-0 text-muted" />
        <span>{t("action_logout")}</span>
      </Command.Item>
    </Command.Group>
  );
}
