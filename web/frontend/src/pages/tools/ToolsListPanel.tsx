import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { RefreshCw, Search, SlidersHorizontal, X } from "lucide-react";
import * as Popover from "@radix-ui/react-popover";
import { tagsApi, toolsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button, Input } from "@/components/ui";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { SectionBoundary } from "@/components/common/SectionBoundary";
import { ToolGroupCard } from "./ToolGroupCard";
import { ToolEditModal } from "./ToolEditModal";
import { PluginsCard } from "./PluginsCard";
import type { ToolGroup } from "@/lib/types";
import type { EditState } from "./types";

const EMPTY_GROUPS: ToolGroup[] = [];

/** 可恢复筛选的工具目录，启停操作串行提交并同步相关视图。 */
export function ToolsListPanel() {
  const { t } = useTranslation(["tools", "common"]);
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const search = params.get("q") ?? "";
  const requestedStatus = params.get("status");
  const status = requestedStatus === "enabled" || requestedStatus === "disabled" ? requestedStatus : "all";
  const tags = useMemo(() => [...new Set(params.getAll("tag").filter(Boolean))], [params]);
  const [tagSearch, setTagSearch] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<EditState | null>(null);
  const updateFilter = (key: string, values: string[]) => setParams((current) => {
    const next = new URLSearchParams(current);
    next.delete(key);
    values.filter(Boolean).forEach((value) => next.append(key, value));
    return next;
  }, { replace: true });
  const resetFilters = () => setParams((current) => {
    const next = new URLSearchParams(current);
    ["q", "status", "tag"].forEach((key) => next.delete(key));
    return next;
  }, { replace: true });
  const query = useQuery({ queryKey: ["tools-grouped"], queryFn: () => toolsApi.grouped().then((r) => r.data), refetchInterval: 10000, throwOnError: false });
  const tagQuery = useQuery({ queryKey: ["tool-tags"], queryFn: () => tagsApi.toolTags().then((r) => r.data), throwOnError: false });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: () => toolsApi.plugins().then((r) => r.data), throwOnError: false });
  const refresh = async () => { await Promise.all([
    client.invalidateQueries({ queryKey: ["tools-grouped"] }),
    client.invalidateQueries({ queryKey: ["entity-detail"] }),
    client.invalidateQueries({ queryKey: ["tool-tags"] }),
    client.invalidateQueries({ queryKey: ["unified-tags"] }),
    client.invalidateQueries({ queryKey: ["plugins"] }),
  ]); };
  const toggle = useMutation({
    mutationFn: ({ name, group }: { name: string; group: boolean }) => group ? toolsApi.toggleGroup(name) : toolsApi.toggle(name),
    onSuccess: refresh,
  });
  const reload = useMutation({ mutationFn: toolsApi.reload, onSuccess: refresh });
  const save = useMutation({
    mutationFn: (state: EditState) => toolsApi.updateMeta(state.name, { tags: state.tags, description: state.description }),
    onSuccess: async () => { await refresh(); setEditing(null); },
  });
  const busy = toggle.isPending || reload.isPending || save.isPending;
  const groups = query.data ?? EMPTY_GROUPS;
  const keyword = search.trim().toLocaleLowerCase();
  const filtered = useMemo(() => groups.flatMap((group) => {
    const groupMatch = [group.group, group.description, t(`groups.${group.group}`, { defaultValue: group.group })].some((value) => value.toLocaleLowerCase().includes(keyword));
    const tools = group.tools.filter((tool) => (status === "all" || tool.enabled === (status === "enabled"))
      && tags.every((tag) => tool.tags.includes(tag))
      && (!keyword || groupMatch || [tool.name, tool.description, ...tool.tags].some((value) => value.toLocaleLowerCase().includes(keyword))));
    return tools.length ? [{ ...group, tools }] : [];
  }), [groups, keyword, status, tags, t]);
  const activeFilters = !!keyword || status !== "all" || tags.length > 0;
  const total = groups.reduce((sum, group) => sum + group.total_count, 0);
  const enabled = groups.reduce((sum, group) => sum + group.enabled_count, 0);
  const matched = filtered.reduce((sum, group) => sum + group.tools.length, 0);
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-muted" role="status">{activeFilters ? t("matchedCount", { count: matched }) : t("enabledCount", { enabled, total })}</p>
      <Button size="sm" onClick={() => reload.mutate()} disabled={busy} loading={reload.isPending}><RefreshCw size={15} />{t("reload")}</Button>
    </div>
    <div className="tools-filterbar">
      <div className="relative min-w-0 flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" />
        <Input value={search} aria-label={t("searchPlaceholder")} placeholder={t("searchPlaceholder")} onChange={(event) => updateFilter("q", [event.target.value])} className="pl-9" />
      </div>
      <select aria-label={t("statusFilter")} value={status} onChange={(event) => updateFilter("status", event.target.value === "all" ? [] : [event.target.value])} className="h-10 rounded-lg border border-input bg-card px-3 text-sm">
        <option value="all">{t("statusAll")}</option><option value="enabled">{t("statusEnabled")}</option><option value="disabled">{t("statusDisabled")}</option>
      </select>
      <Popover.Root><Popover.Trigger asChild><Button aria-label={t("filterTags")}><SlidersHorizontal size={16} />{t("filterTags")}{tags.length > 0 && <span>{tags.length}</span>}</Button></Popover.Trigger>
        <Popover.Portal><Popover.Content align="end" sideOffset={8} className="filter-popover z-[110] w-80 max-w-[calc(100vw-24px)] rounded-xl border border-border bg-card p-3 shadow-lg">
          <Input aria-label={t("searchTags")} placeholder={t("searchTags")} value={tagSearch} onChange={(event) => setTagSearch(event.target.value)} />
          {tagQuery.error ? <QueryError compact error={tagQuery.error} retry={() => void tagQuery.refetch()} /> : <div className="mt-3 flex max-h-64 flex-wrap gap-2 overflow-y-auto">
            {(tagQuery.data ?? []).filter((tag: string) => tag.toLocaleLowerCase().includes(tagSearch.toLocaleLowerCase())).map((tag: string) => <button key={tag} aria-pressed={tags.includes(tag)}
              onClick={() => updateFilter("tag", tags.includes(tag) ? tags.filter((value) => value !== tag) : [...tags, tag])}
              className={cn("filter-chip", tags.includes(tag) && "is-active")}>{tag}</button>)}
          </div>}
        </Popover.Content></Popover.Portal>
      </Popover.Root>
    </div>
    {activeFilters && <div className="flex flex-wrap items-center gap-2">
      {tags.map((tag) => <button key={tag} className="filter-chip is-active" aria-label={t("removeTag", { tag })} onClick={() => updateFilter("tag", tags.filter((value) => value !== tag))}>{tag}<X size={13} /></button>)}
      <Button variant="ghost" size="sm" onClick={resetFilters}>{t("resetFilters")}</Button>
    </div>}
    <AsyncState pending={query.isPending} error={!query.data ? query.error : undefined} retry={() => void query.refetch()}>
      {query.error && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      {filtered.length ? <div className="space-y-3">{filtered.map((group) => <ToolGroupCard key={group.group} group={group}
        isOpen={expanded[group.group] ?? activeFilters} busy={busy}
        onToggle={() => setExpanded((current) => ({ ...current, [group.group]: !(current[group.group] ?? activeFilters) }))}
        onToggleGroup={() => toggle.mutate({ name: group.group, group: true })}
        onToggleTool={(name) => toggle.mutate({ name, group: false })}
        onEditTool={(tool) => { save.reset(); setEditing({ name: tool.name, tags: [...tool.tags], description: tool.description }); }}
        onOpenEntity={(groupName) => navigate(`/entities/${encodeURIComponent(groupName)}`)} />)}</div>
        : <p className="rounded-xl border border-dashed border-border py-12 text-center text-sm text-muted">{t(activeFilters ? "noMatch" : "noTools")}</p>}
    </AsyncState>
    <SectionBoundary>{plugins.error ? <QueryError compact error={plugins.error} retry={() => void plugins.refetch()} /> : plugins.data && <PluginsCard plugins={plugins.data} />}</SectionBoundary>
    {editing && <ToolEditModal editing={editing} onChange={setEditing} onClose={() => { if (!save.isPending) setEditing(null); }} onSave={() => save.mutate(editing)} isPending={save.isPending} error={save.error} />}
  </div>;
}
