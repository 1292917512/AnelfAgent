import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { configMetaApi, type ConfigMetaItem } from "@/lib/api";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Loader2, Search, SlidersHorizontal, X } from "lucide-react";
import { buildConfigTree, findGroupOfKey, searchConfigItems } from "@/pages/config/configTree";
import { ConfigSidebar } from "@/pages/config/ConfigSidebar";
import { ConfigSection } from "@/pages/config/ConfigSection";
import { ConfigDetailDrawer } from "@/pages/config/ConfigDetailDrawer";
import { ConversationWindowRow } from "@/pages/config/ConversationWindowRow";

/** 对话窗口复合行收编的键（一行配置：总条数 + 保留比例滑条） */
const WINDOW_KEYS = new Set(["max_conversation_size", "conversation_raw_keep_percent"]);

/** i18n 资源 key 顺序驱动模块/分组展示顺序（未列出的排最后） */
function useConfigOrder(): { moduleOrder: string[]; sectionOrder: string[] } {
  const { i18n } = useTranslation("config");
  const language = i18n.language;
  return useMemo(() => {
    const bundle = i18n.getResourceBundle(language, "config") as
      | { modules?: Record<string, string>; sections?: Record<string, string> }
      | undefined;
    return {
      moduleOrder: Object.keys(bundle?.modules ?? {}),
      sectionOrder: Object.keys(bundle?.sections ?? {}),
    };
  }, [i18n, language]);
}

export default function Config() {
  const { t } = useTranslation(["config", "common"]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeGroup, setActiveGroup] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ item: ConfigMetaItem; group: string } | null>(null);
  const { moduleOrder, sectionOrder } = useConfigOrder();

  const { data, isLoading } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });

  const tree = useMemo(
    () => buildConfigTree(data?.groups ?? [], moduleOrder, sectionOrder),
    [data, moduleOrder, sectionOrder],
  );

  // 深链定位：?key=xxx → 切到所属分组 + 高亮 + 滚动定位
  useEffect(() => {
    const key = searchParams.get("key");
    if (!key || tree.length === 0) return;
    const group = findGroupOfKey(tree, key);
    if (group) {
      setActiveGroup(group);
      setFocusKey(key);
      setQuery("");
    }
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams, tree]);

  // 高亮行滚动定位
  useEffect(() => {
    if (!focusKey) return;
    const raf = requestAnimationFrame(() => {
      document
        .getElementById(`config-item-${focusKey}`)
        ?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    return () => cancelAnimationFrame(raf);
  }, [focusKey, activeGroup]);

  const searching = query.trim().length > 0;
  const searchResults = useMemo(
    () => (searching ? searchConfigItems(tree, query) : []),
    [tree, query, searching],
  );

  const currentGroup =
    tree.flatMap((m) => m.sections).find((s) => s.group === activeGroup) ??
    tree[0]?.sections[0] ??
    null;

  // 对话窗口复合行：当前分组同时含两个窗口键时收编为一行
  const windowItems = useMemo(() => {
    const items = currentGroup?.items ?? [];
    const sizeItem = items.find((i) => i.key === "max_conversation_size");
    const percentItem = items.find((i) => i.key === "conversation_raw_keep_percent");
    return sizeItem && percentItem ? { sizeItem, percentItem } : null;
  }, [currentGroup]);

  return (
    <PageContainer>
      <PageHeader
        icon={<SlidersHorizontal size={22} />}
        title={t("title")}
        subtitle={t("subtitle")}
      />

      <div className="flex gap-5 items-start">
        {/* 左侧模块树（移动端隐藏，改用下拉选择） */}
        <aside className="hidden md:block w-52 shrink-0 sticky top-4">
          <ConfigSidebar tree={tree} active={currentGroup?.group ?? null} onSelect={setActiveGroup} />
        </aside>

        <div className="flex-1 min-w-0 space-y-4">
          {/* 搜索框 */}
          <div className="relative">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="w-full bg-card border border-border rounded-md pl-9 pr-9 py-2 text-sm text-foreground outline-none focus:border-ring placeholder:text-muted"
            />
            {searching && (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("common:close")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted hover:text-foreground"
              >
                <X size={15} />
              </button>
            )}
          </div>

          {/* 移动端分组下拉 */}
          {!searching && (
            <select
              value={currentGroup?.group ?? ""}
              onChange={(e) => setActiveGroup(e.target.value)}
              className="md:hidden w-full bg-card border border-border rounded-md px-3 py-2 text-sm text-foreground outline-none focus:border-ring"
            >
              {tree.flatMap((m) =>
                m.sections.map((s) => (
                  <option key={s.group} value={s.group}>
                    {t(`modules.${m.module}`, { defaultValue: m.module })} ·{" "}
                    {t(`sections.${s.group}`, { defaultValue: s.group })}
                  </option>
                )),
              )}
            </select>
          )}

          {isLoading ? (
            <div className="flex justify-center py-12 text-muted">
              <Loader2 size={24} className="animate-spin" />
            </div>
          ) : searching ? (
            /* 搜索结果：按分组聚合，高级区默认展开 */
            <div className="space-y-5">
              <div className="text-xs text-muted">
                {t("searchResults", { count: searchResults.reduce((n, s) => n + s.items.length, 0) })}
              </div>
              {searchResults.length === 0 && (
                <div className="py-12 text-center text-sm text-muted">{t("noResult")}</div>
              )}
              {searchResults.map((s) => (
                <div key={s.group} className="space-y-2.5">
                  <div className="text-xs font-medium text-muted">
                    {t(`sections.${s.group}`, { defaultValue: s.group })}
                  </div>
                  <ConfigSection
                    items={s.items}
                    expandAdvanced
                    onOpenDetail={(item) => setDetail({ item, group: s.group })}
                  />
                </div>
              ))}
            </div>
          ) : currentGroup ? (
            <div className="space-y-2.5">
              {windowItems && (
                <div id="config-item-max_conversation_size">
                  <ConversationWindowRow
                    sizeItem={windowItems.sizeItem}
                    percentItem={windowItems.percentItem}
                  />
                </div>
              )}
              <ConfigSection
                items={currentGroup.items}
                focusKey={focusKey}
                renderRow={(item, defaultRow) => (WINDOW_KEYS.has(item.key) ? null : defaultRow)}
                onOpenDetail={(item) => setDetail({ item, group: currentGroup.group })}
              />
            </div>
          ) : null}
        </div>
      </div>

      <ConfigDetailDrawer
        item={detail?.item ?? null}
        group={detail?.group}
        onClose={() => setDetail(null)}
      />
    </PageContainer>
  );
}
