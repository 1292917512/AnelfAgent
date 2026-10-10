import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import { Search, SlidersHorizontal } from "lucide-react";
import { useAppStore } from "@/stores/app-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { PaletteResults } from "./PaletteResults";
import { PaletteActions } from "./PaletteActions";
import { groupCls, itemCls } from "./paletteStyles";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { getNavigation } from "@/lib/navigation";
import { configMetaApi, searchApi } from "@/lib/api";
import type { GlobalSearchResult } from "@/lib/types";

/** 全局命令面板（⌘K / Ctrl+K）：页面导航 + 快捷操作 + 全局搜索 */
export function CommandPalette() {
  const open = useAppStore((s) => s.paletteOpen);
  const setOpen = useAppStore((s) => s.setPaletteOpen);

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GlobalSearchResult | null>(null);
  const seqRef = useRef(0);
  const [searchState, setSearchState] = useState<"idle" | "loading" | "error">("idle");

  // 配置项索引（⌘K 直接定位配置，跳转 /config?key= 深链）
  const { data: configMeta } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
    staleTime: 60_000,
    enabled: open,
    throwOnError: false,
  });
  const configItems = useMemo(
    () => (configMeta?.groups ?? []).flatMap((g) => g.items),
    [configMeta],
  );

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

  useEffect(() => {
    if (!open) { setQuery(""); setResults(null); }
  }, [open]);

  // 全局搜索（防抖 300ms，乱序响应按序号丢弃）
  useEffect(() => {
    const seq = ++seqRef.current;
    const q = query.trim();
    if (!open || q.length < 2) {
      setResults(null);
      setSearchState("idle");
      return;
    }
    setResults(null);
    setSearchState("loading");
    const controller = new AbortController();
    const timer = setTimeout(() => {
      searchApi
        .global(q, 5, controller.signal)
        .then((r) => {
          if (seq === seqRef.current) { setResults(r.data); setSearchState("idle"); }
        })
        .catch(() => {
          if (seq === seqRef.current && !controller.signal.aborted) { setResults(null); setSearchState("error"); }
        });
    }, 300);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, open]);

  if (!open) return null;

  const close = () => setOpen(false);
  const navItems = getNavigation();

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

  return (
    <DialogSurface open={open} onClose={close} title={t("label")} placement="top" className="command-palette max-w-xl overflow-hidden">
        <Command label={t("label")} loop>
          <div className="integrated-field palette-search flex items-center gap-3 px-4 border-b border-border">
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

          {searchState !== "idle" && <p role={searchState === "error" ? "alert" : "status"} className="px-4 py-2 text-xs text-muted">{t(searchState === "loading" ? "searching" : "searchFailed")}</p>}
          <Command.List className="max-h-[52vh] overflow-y-auto p-2">
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted">
              {t("empty")}
            </Command.Empty>

            {hasResults && results && (
              <PaletteResults results={results} query={query.trim()} onGo={go} onOpenSearchPanel={openSearchPanel} />
            )}

            <Command.Group heading={t("group_nav")} className={groupCls}>
              {navItems.map((item) => {
                const Icon = item.icon;
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

            {/* 配置项索引：仅在输入查询时参与过滤，跳转配置中心深链 */}
            {query.trim().length > 0 && (
              <Command.Group heading={t("group_config")} className={groupCls}>
                {configItems.map((item) => (
                  <Command.Item
                    key={item.key}
                    value={`${item.description} ${item.key}`}
                    keywords={[item.key]}
                    className={itemCls}
                    onSelect={() => go(`/config?key=${encodeURIComponent(item.key)}`)}
                  >
                    <SlidersHorizontal size={15} className="shrink-0 text-muted" />
                    <span>{item.description}</span>
                    <span className="ml-auto shrink-0 text-[10px] font-mono text-muted">
                      {item.key}
                    </span>
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>
        </Command>
    </DialogSurface>
  );
}
