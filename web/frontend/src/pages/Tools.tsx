import { useState, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toolsApi, tagsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UnifiedTag } from "@/lib/types";
import {
  ChevronDown,
  ChevronRight,
  ToggleLeft,
  ToggleRight,
  Search,
  RefreshCw,
  Package,
  Wrench,
  Pencil,
  X,
  Tag,
} from "lucide-react";

interface ToolItem {
  name: string;
  source: string;
  enabled: boolean;
  description: string;
  tags: string[];
}

interface ToolGroup {
  group: string;
  description: string;
  tools: ToolItem[];
  all_enabled: boolean;
  any_enabled: boolean;
  enabled_count: number;
  total_count: number;
}

interface PluginInfo {
  name: string;
  version: string;
  author: string;
  enabled: boolean;
  description: string;
}

interface EditState {
  name: string;
  tags: string[];
  description: string;
}

export default function Tools() {
  const { t } = useTranslation(["tools", "common", "tags"]);
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<EditState | null>(null);
  const [activeTagFilters, setActiveTagFilters] = useState<Set<string>>(new Set());
  const [tagFilterOpen, setTagFilterOpen] = useState(true);
  const [tagPickerSearch, setTagPickerSearch] = useState("");

  const { data: groups = [], isLoading } = useQuery<ToolGroup[]>({
    queryKey: ["tools-grouped"],
    queryFn: () => toolsApi.grouped().then((r) => r.data),
    refetchInterval: 10000,
  });

  const { data: allToolTags = [] } = useQuery<string[]>({
    queryKey: ["tool-tags"],
    queryFn: () => tagsApi.toolTags().then((r) => r.data),
  });

  const { data: unifiedTags = [] } = useQuery<UnifiedTag[]>({
    queryKey: ["unified-tags"],
    queryFn: () => tagsApi.unified().then((r) => r.data),
    enabled: !!editing,
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
      toolsApi.updateMeta(state.name, {
        tags: state.tags,
        description: state.description,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tools-grouped"] });
      queryClient.invalidateQueries({ queryKey: ["tool-tags"] });
      queryClient.invalidateQueries({ queryKey: ["unified-tags"] });
      setEditing(null);
      setTagPickerSearch("");
    },
  });

  const toggle = (group: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const openEdit = (tool: ToolItem) => {
    setEditing({ name: tool.name, tags: [...tool.tags], description: tool.description });
    setTagPickerSearch("");
  };

  const toggleTagFilter = (tag: string) => {
    setActiveTagFilters((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  };

  const addTagToEditing = (tag: string) => {
    if (!editing || editing.tags.includes(tag)) return;
    setEditing({ ...editing, tags: [...editing.tags, tag] });
  };

  const removeTagFromEditing = (tag: string) => {
    if (!editing) return;
    setEditing({ ...editing, tags: editing.tags.filter((t) => t !== tag) });
  };

  // Available tags for picker: all unified tags not already selected
  const pickerAvailable = useMemo(() => {
    if (!editing) return [];
    const kw = tagPickerSearch.toLowerCase();
    return unifiedTags.filter(
      (t) => !editing.tags.includes(t.name) && (!kw || t.name.includes(kw)),
    );
  }, [unifiedTags, editing, tagPickerSearch]);

  // Tags currently on the tool but not in the unified registry
  const unknownSelectedTags = useMemo(() => {
    if (!editing) return [];
    const known = new Set(unifiedTags.map((t) => t.name));
    return editing.tags.filter((t) => !known.has(t));
  }, [unifiedTags, editing]);

  const kw = search.toLowerCase();
  const filtered = useMemo(() => {
    return groups
      .map((g) => {
        const groupMatch =
          !kw ||
          g.group.toLowerCase().includes(kw) ||
          g.description.toLowerCase().includes(kw);

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
    <div className="space-y-5 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-xs px-2 py-0.5 rounded-full bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]">
          {t("enabledCount", { enabled: enabledTools, total: totalTools })}
        </span>
        <button
          onClick={() => reloadMut.mutate()}
          disabled={reloadMut.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
            border border-[var(--border)] text-[var(--text)] hover:bg-[var(--bg-hover)] transition-all disabled:opacity-50"
        >
          <RefreshCw size={14} className={reloadMut.isPending ? "animate-spin" : ""} />
          {reloadMut.isPending ? t("reloading") : t("reload")}
        </button>
      </div>

      {/* Search */}
      <div className="relative">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="w-full pl-9 pr-3 py-2 text-sm rounded-[var(--radius-md)]
            border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text)]
            placeholder:text-[var(--muted)] focus:outline-none focus:border-[var(--accent)]"
        />
      </div>

      {/* Tag Filter Bar */}
      {allToolTags.length > 0 && (
        <div className="flex items-start gap-2">
          <button
            onClick={() => setTagFilterOpen((v) => !v)}
            className="flex items-center gap-1 text-[var(--muted)] hover:text-[var(--text)] transition-colors mt-0.5 flex-shrink-0"
            title={tagFilterOpen ? "收起标签筛选" : "展开标签筛选"}
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
                        ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                        : "bg-[var(--secondary)] text-[var(--muted)] border-[var(--border)] hover:border-[var(--accent)] hover:text-[var(--accent)]",
                    )}
                  >
                    {tag}
                  </button>
                );
              })}
              {activeTagFilters.size > 0 && (
                <button
                  onClick={() => setActiveTagFilters(new Set())}
                  className="text-[11px] px-2 py-0.5 rounded-full border border-[var(--border)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-all flex items-center gap-1"
                >
                  <X size={10} />
                  {t("common:all")}
                </button>
              )}
            </div>
          ) : (
            <span className="text-[11px] text-[var(--muted)] mt-0.5">
              {allToolTags.length} 个标签
              {activeTagFilters.size > 0 && (
                <span className="ml-1.5 text-[var(--accent)]">
                  （已激活 {activeTagFilters.size} 个）
                </span>
              )}
            </span>
          )}
        </div>
      )}

      {isLoading ? (
        <p className="text-sm text-[var(--muted)]">{t("common:loading")}</p>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-[var(--muted)] text-center py-8">
          {search ? t("noMatch") : t("noTools")}
        </p>
      ) : (
        <div className="space-y-2">
          {filtered.map((g) => {
            const isOpen = expanded.has(g.group) || !!kw;
            return (
              <div
                key={g.group}
                className={cn(
                  "rounded-[var(--radius-md)] border transition-all bg-[var(--card)]",
                  isOpen
                    ? "border-[var(--accent)] shadow-[0_0_0_1px_var(--ring)]"
                    : "border-[var(--border)] hover:border-[var(--border-strong)]",
                )}
              >
                {/* Group header */}
                <div
                  className="flex items-center justify-between px-4 py-3 cursor-pointer select-none"
                  onClick={() => toggle(g.group)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <ChevronDown
                      size={16}
                      className={cn(
                        "text-[var(--muted)] transition-transform flex-shrink-0",
                        isOpen && "rotate-180",
                      )}
                    />
                    <Package size={16} className="text-[var(--accent)] flex-shrink-0" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-[var(--text-strong)]">
                          {t(`groups.${g.group}`, { defaultValue: g.group })}
                        </span>
                        <span className="text-[11px] px-1.5 py-0.5 rounded bg-[var(--secondary)] text-[var(--muted)]">
                          {g.enabled_count}/{g.total_count}
                        </span>
                      </div>
                      {g.description && (
                        <p className="text-[11px] text-[var(--muted)] truncate mt-0.5">
                          {g.description}
                        </p>
                      )}
                    </div>
                  </div>

                  <div
                    className="flex items-center gap-2 flex-shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <button
                      onClick={() => toggleGroupMut.mutate(g.group)}
                      className={cn(
                        "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                        g.any_enabled
                          ? g.all_enabled
                            ? "bg-[var(--accent)]"
                            : "bg-[var(--accent)] opacity-60"
                          : "bg-[var(--secondary)] border border-[var(--border)]",
                      )}
                      title={g.all_enabled ? t("disableGroup") : t("enableGroup")}
                    >
                      <span
                        className={cn(
                          "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform",
                          g.any_enabled ? "translate-x-[18px]" : "translate-x-[3px]",
                        )}
                      />
                    </button>
                  </div>
                </div>

                {/* Tools list */}
                {isOpen && (
                  <div className="border-t border-[var(--border)]">
                    {g.tools.map((tool, idx) => (
                      <div
                        key={tool.name}
                        className={cn(
                          "flex items-center justify-between px-4 py-2.5 hover:bg-[var(--bg-hover)] transition-colors",
                          idx < g.tools.length - 1 && "border-b border-[var(--border)]",
                        )}
                      >
                        <div className="flex items-center gap-3 min-w-0 mr-3 flex-1">
                          <Wrench
                            size={13}
                            className={cn(
                              "flex-shrink-0",
                              tool.enabled ? "text-[var(--ok)]" : "text-[var(--muted)]",
                            )}
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="text-sm font-medium text-[var(--text-strong)] font-mono">
                                {tool.name}
                              </span>
                              {tool.tags.map((tag) => (
                                <span
                                  key={tag}
                                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-[var(--accent-subtle,var(--secondary))] text-[var(--accent)] border border-[var(--ring,var(--border))]"
                                >
                                  {tag}
                                </span>
                              ))}
                            </div>
                            {tool.description && (
                              <p className="text-[11px] text-[var(--muted)] truncate mt-0.5">
                                {tool.description}
                              </p>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <button
                            onClick={() => openEdit(tool)}
                            className="text-[var(--muted)] hover:text-[var(--text)] transition-colors p-1"
                            title={t("editProperties")}
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            onClick={() => toggleToolMut.mutate(tool.name)}
                            className={cn(
                              "transition-colors",
                              tool.enabled ? "text-[var(--ok)]" : "text-[var(--muted)]",
                            )}
                            title={tool.enabled ? t("disable") : t("enable")}
                          >
                            {tool.enabled ? (
                              <ToggleRight size={22} />
                            ) : (
                              <ToggleLeft size={22} />
                            )}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Plugins */}
      {plugins.length > 0 && (
        <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)] overflow-hidden">
          <div className="px-4 py-3 border-b border-[var(--border)]">
            <span className="text-sm font-semibold text-[var(--text-strong)]">{t("pluginsTitle")}</span>
            <span className="ml-2 text-[11px] text-[var(--muted)]">
              {t("pluginsLoaded", { count: plugins.length })}
            </span>
          </div>
          <div>
            {plugins.map((p, idx) => (
              <div
                key={p.name}
                className={cn(
                  "flex items-center justify-between px-4 py-2.5",
                  idx < plugins.length - 1 && "border-b border-[var(--border)]",
                )}
              >
                <div>
                  <span className="font-medium text-sm text-[var(--text-strong)]">{p.name}</span>
                  <span className="ml-2 text-xs text-[var(--muted)]">v{p.version}</span>
                  {p.description && (
                    <p className="text-[11px] text-[var(--muted)] mt-0.5">{p.description}</p>
                  )}
                </div>
                <span
                  className={cn(
                    "text-xs font-medium px-2 py-0.5 rounded-full",
                    p.enabled
                      ? "bg-[var(--ok-subtle)] text-[var(--ok)] border border-[rgba(34,197,94,0.3)]"
                      : "bg-[var(--secondary)] text-[var(--muted)] border border-[var(--border)]",
                  )}
                >
                  {p.enabled ? t("pluginEnabled") : t("pluginDisabled")}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Edit Modal */}
      {editing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-[var(--card)] rounded-[var(--radius-lg,12px)] border border-[var(--border)] shadow-xl w-full max-w-lg mx-4 p-6">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Tag size={18} className="text-[var(--accent)]" />
                <h3 className="text-base font-semibold text-[var(--text-strong)]">
                  {t("editToolProps")}
                </h3>
              </div>
              <button
                onClick={() => { setEditing(null); setTagPickerSearch(""); }}
                className="text-[var(--muted)] hover:text-[var(--text)] transition-colors"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-4">
              {/* Tool name */}
              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-1">
                  {t("toolName")}
                </label>
                <div className="text-sm font-mono text-[var(--text-strong)] px-3 py-2 rounded-[var(--radius-md)] bg-[var(--secondary)]">
                  {editing.name}
                </div>
              </div>

              {/* Tag picker */}
              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-1">
                  {t("tagsLabel")}
                </label>

                {/* Selected tags */}
                <div className="min-h-[36px] p-2 rounded-t-[var(--radius-md)] border border-b-0 border-[var(--border)] bg-[var(--secondary)] flex flex-wrap gap-1.5">
                  {editing.tags.length === 0 ? (
                    <span className="text-[11px] text-[var(--muted)] self-center px-1">
                      {t("tags:selectedTags")}…
                    </span>
                  ) : (
                    editing.tags.map((tag) => (
                      <span
                        key={tag}
                        className={cn(
                          "inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border",
                          unknownSelectedTags.includes(tag)
                            ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                            : "bg-[var(--accent)]/10 text-[var(--accent)] border-[var(--accent)]/30",
                        )}
                      >
                        {tag}
                        <button
                          onClick={() => removeTagFromEditing(tag)}
                          className="hover:text-red-400 transition-colors"
                        >
                          <X size={9} />
                        </button>
                      </span>
                    ))
                  )}
                </div>

                {/* Available tags search + list */}
                <div className="border border-[var(--border)] rounded-b-[var(--radius-md)] bg-[var(--bg-elevated)] overflow-hidden">
                  <div className="relative border-b border-[var(--border)]">
                    <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
                    <input
                      value={tagPickerSearch}
                      onChange={(e) => setTagPickerSearch(e.target.value)}
                      placeholder={t("tags:filterPlaceholder")}
                      className="w-full pl-7 pr-3 py-1.5 text-xs bg-transparent text-[var(--text)]
                        placeholder:text-[var(--muted)] focus:outline-none"
                    />
                  </div>
                  <div className="p-2 flex flex-wrap gap-1.5 max-h-32 overflow-y-auto">
                    {pickerAvailable.length === 0 ? (
                      <span className="text-[11px] text-[var(--muted)] px-1">
                        {t("tags:noAvailableTags")}
                      </span>
                    ) : (
                      pickerAvailable.map((tag) => (
                        <button
                          key={tag.name}
                          onClick={() => addTagToEditing(tag.name)}
                          className="text-[11px] px-2.5 py-0.5 rounded-full border border-[var(--border)]
                            bg-[var(--secondary)] text-[var(--muted)]
                            hover:border-[var(--accent)] hover:text-[var(--accent)] hover:bg-[var(--accent)]/5
                            transition-all"
                        >
                          {tag.name}
                        </button>
                      ))
                    )}
                  </div>
                </div>
              </div>

              {/* Description */}
              <div>
                <label className="block text-xs font-medium text-[var(--muted)] mb-1">
                  {t("toolDescription")}
                </label>
                <textarea
                  value={editing.description}
                  onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                  rows={3}
                  className="w-full px-3 py-2 text-sm rounded-[var(--radius-md)]
                    border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--text)]
                    placeholder:text-[var(--muted)] focus:outline-none focus:border-[var(--accent)] resize-none"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 mt-5">
              <button
                onClick={() => { setEditing(null); setTagPickerSearch(""); }}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  border border-[var(--border)] text-[var(--text)] hover:bg-[var(--bg-hover)] transition-all"
              >
                {t("common:cancel")}
              </button>
              <button
                onClick={() => editing && updateMetaMut.mutate(editing)}
                disabled={updateMetaMut.isPending}
                className="px-4 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
                  bg-[var(--accent)] text-white hover:opacity-90 transition-all disabled:opacity-50"
              >
                {updateMetaMut.isPending ? t("common:saving") : t("common:save")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
