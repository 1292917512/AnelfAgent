import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, RefreshCw, Search, Tag, X } from "lucide-react";
import { tagsApi, toolsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button, Input, LoadingBlock } from "@/components/ui";
import { ToolGroupCard } from "./ToolGroupCard";
import { ToolEditModal } from "./ToolEditModal";
import { PluginsCard } from "./PluginsCard";
import type { EditState, PluginInfo, ToolGroup, ToolItem } from "./types";

/** 工具列表面板：搜索 + 标签筛选 + 分组手风琴 + 插件 */
export function ToolsListPanel() {
  const { t } = useTranslation(["tools", "common"]);
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<EditState | null>(null);
  const [activeTagFilters, setActiveTagFilters] = useState<Set<string>>(new Set());
  const [tagFilterOpen, setTagFilterOpen] = useState(true);

  const { data: groups = [], isLoading } = useQuery<ToolGroup[]>({
    queryKey: ["tools-grouped"],
    queryFn: () => toolsApi.grouped().then((r) => r.data),
    refetchInterval: 10000,
  });

  const { data: allToolTags = [] } = useQuery<string[]>({
    queryKey: ["tool-tags"],
    queryFn: () => tagsApi.toolTags().then((r) => r.data),
  });

  const { data: plugins = [] } = useQuery<PluginInfo[]>({
    queryKey: ["plugins"],
    queryFn: () => toolsApi.plugins().then((r) => r.data),
  });

  const toggleToolMut = useMutation({
    mutationFn: (name: string) => toolsApi.toggle(name),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tools-grouped"] }),
  });

  const toggleGroupMut = useMutation({
    mutationFn: (group: string) => toolsApi.toggleGroup(group),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tools-grouped"] }),
  });

  const reloadMut = useMutation({
    mutationFn: () => toolsApi.reload(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tools-grouped"] }),
  });

  const updateMetaMut = useMutation({
    mutationFn: (state: EditState) =>
      toolsApi.updateMeta(state.name, { tags: state.tags, description: state.description }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tools-grouped"] });
      queryClient.invalidateQueries({ queryKey: ["tool-tags"] });
      queryClient.invalidateQueries({ queryKey: ["unified-tags"] });
      setEditing(null);
    },
  });

  const toggleExpand = (group: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const toggleTagFilter = (tag: string) => {
    setActiveTagFilters((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const kw = search.toLowerCase();
  const filtered = useMemo(() => {
    return groups
      .map((g) => {
        const groupMatch =
          !kw || g.group.toLowerCase().includes(kw) || g.description.toLowerCase().includes(kw);

        const matchedTools = g.tools.filter((tool) => {
          const kwMatch =
            !kw ||
            tool.name.toLowerCase().includes(kw) ||
            tool.description.toLowerCase().includes(kw) ||
            tool.tags.some((tag) => tag.toLowerCase().includes(kw));

          const tagMatch =
            activeTagFilters.size === 0 ||
            [...activeTagFilters].every((f) => tool.tags.includes(f));

          return kwMatch && tagMatch;
        });

        if (activeTagFilters.size === 0 && groupMatch) return g;
        if (matchedTools.length > 0) return { ...g, tools: matchedTools };
        return null;
      })
      .filter(Boolean) as ToolGroup[];
  }, [groups, kw, activeTagFilters]);

  const totalTools = groups.reduce((s, g) => s + g.total_count, 0);
  const enabledTools = groups.reduce((s, g) => s + g.enabled_count, 0);

  return (
    <div className="space-y-5">
      {/* 统计 + 重载 */}
      <div className="flex items-center justify-between">
        <span className="text-xs px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
          {t("enabledCount", { enabled: enabledTools, total: totalTools })}
        </span>
        <Button variant="secondary" size="sm" onClick={() => reloadMut.mutate()} loading={reloadMut.isPending}>
          <RefreshCw size={14} className={reloadMut.isPending ? "animate-spin" : ""} />
          {reloadMut.isPending ? t("reloading") : t("reload")}
        </Button>
      </div>

      {/* 搜索 */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="pl-9"
        />
      </div>

      {/* 标签筛选栏 */}
      {allToolTags.length > 0 && (
        <div className="flex items-start gap-2">
          <button
            onClick={() => setTagFilterOpen((v) => !v)}
            className="flex items-center gap-1 text-muted hover:text-foreground transition-colors mt-0.5 flex-shrink-0"
          >
            {tagFilterOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <Tag size={13} />
          </button>

          {tagFilterOpen ? (
            <div className="flex flex-wrap gap-1.5">
              {allToolTags.map((tag) => {
                const active = activeTagFilters.has(tag);
                return (
                  <button
                    key={tag}
                    onClick={() => toggleTagFilter(tag)}
                    className={cn(
                      "text-[11px] px-2.5 py-0.5 rounded-full border transition-all",
                      active
                        ? "bg-accent text-white border-accent"
                        : "bg-secondary text-muted border-border hover:border-accent hover:text-accent",
                    )}
                  >
                    {tag}
                  </button>
                );
              })}
              {activeTagFilters.size > 0 && (
                <button
                  onClick={() => setActiveTagFilters(new Set())}
                  className="text-[11px] px-2 py-0.5 rounded-full border border-border text-muted hover:text-foreground hover:bg-hover transition-all flex items-center gap-1"
                >
                  <X size={10} />
                  {t("common:all")}
                </button>
              )}
            </div>
          ) : (
            <span className="text-[11px] text-muted mt-0.5">
              {t("tagCount", { count: allToolTags.length })}
              {activeTagFilters.size > 0 && (
                <span className="ml-1.5 text-accent">
                  {t("tagActiveCount", { count: activeTagFilters.size })}
                </span>
              )}
            </span>
          )}
        </div>
      )}

      {isLoading ? (
        <LoadingBlock label={t("common:loading")} />
      ) : filtered.length === 0 ? (
        <p className="text-sm text-muted text-center py-8">
          {search ? t("noMatch") : t("noTools")}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((g) => (
            <ToolGroupCard
              key={g.group}
              group={g}
              isOpen={expanded.has(g.group) || !!kw}
              onToggle={() => toggleExpand(g.group)}
              onToggleGroup={() => toggleGroupMut.mutate(g.group)}
              onToggleTool={(name) => toggleToolMut.mutate(name)}
              onEditTool={(tool: ToolItem) =>
                setEditing({ name: tool.name, tags: [...tool.tags], description: tool.description })
              }
            />
          ))}
        </div>
      )}

      <PluginsCard plugins={plugins} />

      {editing && (
        <ToolEditModal
          editing={editing}
          onChange={setEditing}
          onClose={() => setEditing(null)}
          onSave={() => updateMetaMut.mutate(editing)}
          isPending={updateMetaMut.isPending}
        />
      )}
    </div>
  );
}
