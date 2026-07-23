import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Command } from "cmdk";
import {
  Search,
  Sun,
  Moon,
  Languages,
  PanelLeft,
  LogOut,
  LayoutDashboard,
  Brain,
  MessageSquare,
  FileText,
  ArrowRight,
} from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { FALLBACK_NAV, ICON_MAP } from "../layout/Sidebar";
import { searchApi } from "@/lib/api";
import type { GlobalSearchResult } from "@/lib/types";

const itemCls =
  "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm text-foreground cursor-pointer select-none " +
  "aria-selected:bg-accent-subtle aria-selected:text-accent data-[disabled]:opacity-50 data-[disabled]:pointer-events-none";
const groupCls =
  "[&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 " +
  "[&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold " +
  "[&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-muted-strong";

/** 全局命令面板（⌘K / Ctrl+K）：页面导航 + 快捷操作 + 全局搜索 */
export function CommandPalette() {
  const open = useAppStore((s) => s.paletteOpen);
  const setOpen = useAppStore((s) => s.setPaletteOpen);
  const theme = useAppStore((s) => s.theme);
  const toggleTheme = useAppStore((s) => s.toggleTheme);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const navigation = useAppStore((s) => s.navigation);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResult | null>(null);
  const seqRef = useRef(0);

  const navigate = useNavigate();
  const { t, i18n } = useTranslation("palette");
  const { t: tNav } = useTranslation("nav");

  // 全局快捷键：⌘K / Ctrl+K 开关
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        const { paletteOpen, setPaletteOpen } = useAppStore.getState();
        setPaletteOpen(!paletteOpen);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // 打开时锁定背景滚动；关闭时重置输入与结果
  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      setQuery("");
      setResults(null);
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  // 全局搜索（防抖 300ms，乱序响应按序号丢弃）
  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults(null);
      return;
    }
    const seq = ++seqRef.current;
    const timer = setTimeout(() => {
      searchApi
        .global(q, 5)
        .then((r) => {
          if (seq === seqRef.current) setResults(r.data);
        })
        .catch(() => {
          if (seq === seqRef.current) setResults(null);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  if (!open) return null;

  const close = () => setOpen(false);
  const navItems = navigation.length > 0 ? navigation : FALLBACK_NAV;

  /** 跳转并关闭面板 */
  const go = (path: string) => {
    close();
    navigate(path);
  };

  /** 打开聊天页的全局搜索面板 */
  const openSearchPanel = (q: string) => {
    close();
    navigate("/");
    useWorkbenchStore.getState().openPanel("search", q);
  };

  const hasResults =
    results !== null &&
    (results.memory.length > 0 ||
      results.conversations.length > 0 ||
      results.files.length > 0);

  return createPortal(
    <div
      className="fixed inset-0 z-[150] flex items-start justify-center bg-black/50 animate-fade-in px-3 pt-[12vh] sm:pt-[15vh]"
      onClick={close}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="w-full max-w-lg bg-card border border-border rounded-lg shadow-lg animate-rise overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <Command label={t("label")} loop>
          <div className="flex items-center gap-2 px-3 border-b border-border">
            <Search size={16} className="shrink-0 text-muted" />
            <Command.Input
              autoFocus
              value={query}
              onValueChange={setQuery}
              placeholder={t("placeholder")}
              className="w-full bg-transparent py-3 text-sm text-foreground outline-none placeholder:text-muted"
              onKeyDown={(e) => {
                if (e.key === "Escape") close();
              }}
            />
            <kbd className="shrink-0 rounded border border-border bg-elevated px-1.5 py-0.5 text-[10px] font-mono text-muted">
              ESC
            </kbd>
          </div>

          <Command.List className="max-h-[52vh] overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted">
              {t("empty")}
            </Command.Empty>

            {hasResults && results && (
              <Command.Group heading={t("group_results")} className={groupCls}>
                {results.memory.slice(0, 3).map((m) => (
                  <Command.Item
                    key={`mem-${m.id}`}
                    value={`memory ${m.snippet.slice(0, 60)}`}
                    className={itemCls}
                    onSelect={() => go("/memory")}
                  >
                    <Brain size={15} className="shrink-0 text-muted" />
                    <span className="truncate">{m.snippet}</span>
                    <span className="ml-auto shrink-0 text-[10px] text-muted">
                      {t("result_memory")}
                    </span>
                  </Command.Item>
                ))}
                {results.conversations.slice(0, 3).map((c) => (
                  <Command.Item
                    key={`conv-${c.id}`}
                    value={`conversation ${c.snippet.slice(0, 60)}`}
                    className={itemCls}
                    onSelect={() => openSearchPanel(query.trim())}
                  >
                    <MessageSquare size={15} className="shrink-0 text-muted" />
                    <span className="truncate">{c.snippet}</span>
                    <span className="ml-auto shrink-0 text-[10px] text-muted">
                      {t("result_conversation")}
                    </span>
                  </Command.Item>
                ))}
                {results.files.slice(0, 3).map((f, i) => (
                  <Command.Item
                    key={`file-${f.path}-${i}`}
                    value={`file ${f.path}`}
                    className={itemCls}
                    onSelect={() => openSearchPanel(query.trim())}
                  >
                    <FileText size={15} className="shrink-0 text-muted" />
                    <span className="truncate">{f.path}</span>
                    <span className="ml-auto shrink-0 text-[10px] text-muted">
                      {t("result_file")}
                    </span>
                  </Command.Item>
                ))}
                <Command.Item
                  value={`search-all ${query}`}
                  className={itemCls}
                  onSelect={() => openSearchPanel(query.trim())}
                >
                  <ArrowRight size={15} className="shrink-0 text-muted" />
                  <span>{t("open_search", { query: query.trim() })}</span>
                </Command.Item>
              </Command.Group>
            )}

            <Command.Group heading={t("group_nav")} className={groupCls}>
              {navItems.map((item) => {
                const Icon = ICON_MAP[item.icon] ?? LayoutDashboard;
                const label = tNav(item.label, { defaultValue: item.label });
                return (
                  <Command.Item
                    key={item.path}
                    value={`${label} ${item.path}`}
                    keywords={[item.path, item.label]}
                    className={itemCls}
                    onSelect={() => go(item.path)}
                  >
                    <Icon size={15} className="shrink-0 text-muted" />
                    <span>{label}</span>
                    <span className="ml-auto shrink-0 text-[10px] font-mono text-muted">
                      {item.path}
                    </span>
                  </Command.Item>
                );
              })}
            </Command.Group>

            <Command.Group heading={t("group_actions")} className={groupCls}>
              <Command.Item
                value={theme === "dark" ? t("action_theme_light") : t("action_theme_dark")}
                className={itemCls}
                onSelect={() => {
                  toggleTheme();
                  close();
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
                  close();
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
                  close();
                }}
              >
                <PanelLeft size={15} className="shrink-0 text-muted" />
                <span>{t("action_sidebar")}</span>
              </Command.Item>
              <Command.Item
                value={t("action_search")}
                className={itemCls}
                onSelect={() => openSearchPanel("")}
              >
                <Search size={15} className="shrink-0 text-muted" />
                <span>{t("action_search")}</span>
              </Command.Item>
              <Command.Item
                value={t("action_logout")}
                className={itemCls}
                onSelect={() => {
                  close();
                  void useAuthStore.getState().logout();
                }}
              >
                <LogOut size={15} className="shrink-0 text-muted" />
                <span>{t("action_logout")}</span>
              </Command.Item>
            </Command.Group>
          </Command.List>
        </Command>
      </div>
    </div>,
    document.body,
  );
}
