import { Select } from "@/components/ui/Select";
import { PopoverContent } from "@/components/ui/PopoverContent";
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
import { refreshToolCatalog, useEntityReload } from "./useEntityReload";

const EMPTY_GROUPS: ToolGroup[] = [];

/** 可恢复筛选的工具目录，启停操作串行提交并同步相关视图。 */
export function ToolsListPanel() {
  const { t } = useTranslation(["tools", "common"]);
  const navigate = useNavigate();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  // 检索词以本地状态为即时事实源,URL 参数只是持久化副本(挂载时恢复,供刷新与分享)。
  // 受控值若由 URL 异步状态直接回喂,导航迟到落地会用旧值覆写输入框,打断 IME 组合输入。
  const [search, setSearch] = useState(() => params.get("q") ?? "");
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
  const resetFilters = () => {
    setSearch("");
    setParams((current) => {
      const next = new URLSearchParams(current);
      ["q", "status", "tag"].forEach((key) => next.delete(key));
      return next;
    }, { replace: true });
  };
  const query = useQuery({ queryKey: ["tools-grouped"], queryFn: () => toolsApi.grouped().then((r) => r.data), refetchInterval: 10000, throwOnError: false });
  const tagQuery = useQuery({ queryKey: ["tool-tags"], queryFn: () => tagsApi.toolTags().then((r) => r.data), throwOnError: false });
  const plugins = useQuery({ queryKey: ["plugins"], queryFn: () => toolsApi.plugins().then((r) => r.data), throwOnError: false });
  const refresh = () => refreshToolCatalog(client);
  const toggle = useMutation({
    mutationFn: ({ name, group }: { name: string; group: boolean }) => group ? toolsApi.toggleGroup(name) : toolsApi.toggle(name),
    onSuccess: refresh,
  });
  const reload = useEntityReload();
  const save = useMutation({
    mutationFn: (state: EditState) => toolsApi.updateMeta(state.name, { tags: state.tags, description: state.description }),
    onSuccess: refresh,
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
      <Button size="sm" onClick={() => reload.mutate(null)} disabled={busy} loading={reload.isPending && reload.variables === null}><RefreshCw size={15} />{t("reload")}</Button>
    </div>
    <p className="text-xs leading-relaxed text-muted">{t("reloadHint")}</p>
    <div className="tools-filterbar">
      <div className="relative min-w-0 flex-1"><Search size={16} className="pointer-events-none absolute left-3 top-3 text-muted" />
        <Input value={search} aria-label={t("searchPlaceholder")} placeholder={t("searchPlaceholder")} onChange={(event) => { setSearch(event.target.value); updateFilter("q", [event.target.value]); }} className="pl-9" />
      </div>
      <Select aria-label={t("statusFilter")} value={status} onChange={(event) => updateFilter("status", event.target.value === "all" ? [] : [event.target.value])} >
        <option value="all">{t("statusAll")}</option><option value="enabled">{t("statusEnabled")}</option><option value="disabled">{t("statusDisabled")}</option>
      </Select>
      <Popover.Root><Popover.Trigger asChild><Button aria-label={t("filterTags")}><SlidersHorizontal size={16} />{t("filterTags")}{tags.length > 0 && <span>{tags.length}</span>}</Button></Popover.Trigger>
        <PopoverContent align="end" className="filter-popover w-80 p-3">
          <Input aria-label={t("searchTags")} placeholder={t("searchTags")} value={tagSearch} onChange={(event) => setTagSearch(event.target.value)} />
          {tagQuery.error ? <QueryError compact error={tagQuery.error} retry={() => void tagQuery.refetch()} /> : <div className="mt-3 flex max-h-64 flex-wrap gap-2 overflow-y-auto">
            {(tagQuery.data ?? []).filter((tag: string) => tag.toLocaleLowerCase().includes(tagSearch.toLocaleLowerCase())).map((tag: string) => <button key={tag} aria-pressed={tags.includes(tag)}
              onClick={() => updateFilter("tag", tags.includes(tag) ? tags.filter((value) => value !== tag) : [...tags, tag])}
              className={cn("filter-chip", tags.includes(tag) && "is-active")}>{tag}</button>)}
          </div>}
        </PopoverContent>
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
        reloading={reload.isPending && reload.variables === group.reload_module}
        onReload={(module) => reload.mutate(module)}
        onToggle={() => setExpanded((current) => ({ ...current, [group.group]: !(current[group.group] ?? activeFilters) }))}
        onToggleGroup={() => toggle.mutate({ name: group.group, group: true })}
        onToggleTool={(name) => toggle.mutate({ name, group: false })}
        onEditTool={(tool) => { save.reset(); setEditing({ name: tool.name, tags: [...tool.tags], description: tool.description }); }}
        onOpenEntity={(groupName) => navigate(`/entities/${encodeURIComponent(groupName)}`)} />)}</div>
        : <p className="rounded-xl border border-dashed border-border py-12 text-center text-sm text-muted">{t(activeFilters ? "noMatch" : "noTools")}</p>}
    </AsyncState>
    <SectionBoundary>{plugins.error ? <QueryError compact error={plugins.error} retry={() => void plugins.refetch()} /> : plugins.data && <PluginsCard plugins={plugins.data} />}</SectionBoundary>
    {editing && <ToolEditModal editing={editing} onChange={setEditing} onClose={() => setEditing(null)} onSave={(onSaved) => save.mutate(editing, { onSuccess: onSaved })} isPending={save.isPending} error={save.error} />}
  </div>;
}
