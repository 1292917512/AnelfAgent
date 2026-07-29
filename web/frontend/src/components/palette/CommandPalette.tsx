import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Command } from "cmdk";
import { Search, LayoutDashboard } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { PaletteResults } from "./PaletteResults";
import { PaletteActions } from "./PaletteActions";
import { groupCls, itemCls } from "./paletteStyles";
import { FALLBACK_NAV, ICON_MAP } from "../layout/Sidebar";
import { searchApi } from "@/lib/api";
import type { GlobalSearchResult } from "@/lib/types";

/** 全局命令面板（⌘K / Ctrl+K）：页面导航 + 快捷操作 + 全局搜索 */
export function CommandPalette() {
  const open = useAppStore((s) => s.paletteOpen);
  const setOpen = useAppStore((s) => s.setPaletteOpen);
  const navigation = useAppStore((s) => s.navigation);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResult | null>(null);
  const seqRef = useRef(0);

  const navigate = useNavigate();
  const { t } = useTranslation("palette");
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
              <PaletteResults results={results} query={query.trim()} onGo={go} onOpenSearchPanel={openSearchPanel} />
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

            <PaletteActions onClose={close} onOpenSearchPanel={openSearchPanel} />
          </Command.List>
        </Command>
      </div>
    </div>,
    document.body,
  );
}
