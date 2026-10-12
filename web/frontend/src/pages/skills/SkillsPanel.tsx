import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { GraduationCap, Plus, ChevronDown, SlidersHorizontal, Search } from "lucide-react";
import { skillsApi } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { Button, EmptyState, Input, Select } from "@/components/ui";
import { AsyncState } from "@/components/common/AsyncState";
import { TabBar } from "@/components/common/TabBar";
import { VectorBuildCard, HealthStrip } from "./SkillsHealth";
import { SkillCard } from "./SkillCard";
import { SkillEditor } from "./SkillEditor";
import { SkillActivityPanel } from "./SkillActivityPanel";

type LibraryFilter = "all" | "active" | "stale" | "archived";

export function SkillsPanel() {
  const { t } = useTranslation("skills");
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const selected = params.get("skill");
  const stateParam = params.get("state");
  const state: LibraryFilter = stateParam === "active" || stateParam === "stale" || stateParam === "archived" ? stateParam : "all";
  const keyword = params.get("q") ?? "";
  const [sort, setSort] = useState("recent");
  const [editor, setEditor] = useState<{ skill: SkillItem | null } | null>(null);
  const query = useQuery({
    queryKey: ["skills", "list"], queryFn: () => skillsApi.list(true).then((r) => r.data), throwOnError: false,
  });
  const filter = keyword.trim().toLocaleLowerCase();
  const skills = useMemo(() => (query.data ?? []).filter((skill) =>
    (state === "all" || skill.state === state) && (!filter ||
      [skill.name, skill.description, ...skill.trigger_patterns].some((text) => text.toLocaleLowerCase().includes(filter))))
    .sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      const difference = sort === "used" ? b.use_count - a.use_count : sort === "matched" ? b.match_count - a.match_count :
        Math.max(b.last_activity_at, b.last_match_at) - Math.max(a.last_activity_at, a.last_match_at);
      return difference || a.name.localeCompare(b.name);
    }), [query.data, state, filter, sort]);
  const select = (name: string | null, resetFilters = false) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (name) next.set("skill", name); else next.delete("skill");
    if (resetFilters) { next.delete("state"); next.delete("q"); }
    return next;
  }, { replace: true });
  const setFilter = (key: "state" | "q", value: string) => setParams((current) => {
    const next = new URLSearchParams(current);
    if (value) next.set(key, value); else next.delete(key);
    next.delete("skill");
    return next;
  }, { replace: true });
  const invalidate = (name?: string) => {
    void client.invalidateQueries({ queryKey: ["skills"] });
    if (name) void client.invalidateQueries({ queryKey: ["skill", name] });
  };
  const filters: LibraryFilter[] = ["all", "active", "stale", "archived"];
  return <div className="skills-library">
    <SkillActivityPanel />
    <div className="skills-library-heading"><div><h2>{t("library.title")}</h2><p>{t("library.hint")}</p></div>
      <Button variant="primary" onClick={() => setEditor({ skill: null })}><Plus size={16} />{t("createNew")}</Button></div>
    <div className="skills-toolbar">
      <div className="relative min-w-0 flex-1"><Search size={15} className="absolute left-3 top-3 text-muted" /><Input aria-label={t("searchPlaceholder")} value={keyword} onChange={(event) => setFilter("q", event.target.value)} placeholder={t("searchPlaceholder")} className="pl-9" /></div>
      <Select aria-label={t("library.sort")} value={sort} onChange={(event) => setSort(event.target.value)}>
        {(["recent", "used", "matched", "name"] as const).map((key) => <option key={key} value={key}>{t(`library.sorts.${key}`)}</option>)}
      </Select>
    </div>
    <TabBar activeTab={state} onChange={(value) => setFilter("state", value)} tabs={filters.map((key) => ({ key, label: `${t(`library.filters.${key}`)} · ${(query.data ?? []).filter((skill) => key === "all" || skill.state === key).length}` }))} />
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      {!skills.length && <EmptyState icon={GraduationCap} title={filter || state !== "all" ? t("library.noMatches") : t("empty")} />}
      {selected && query.data && !query.data.some((skill) => skill.name === selected) && <p className="skills-caption" role="status">{t("library.missing", { name: selected })}</p>}
      <div className="skills-list">{skills.map((skill) => <SkillCard key={skill.name} skill={skill} expanded={selected === skill.name}
        onToggle={() => select(selected === skill.name ? null : skill.name)} onEdit={(detail) => setEditor({ skill: detail })}
        onChanged={() => invalidate(skill.name)} onDeleted={() => { if (selected === skill.name) select(null); invalidate(skill.name); }} />)}</div>
    </AsyncState>
    <details className="skills-maintenance"><summary><SlidersHorizontal size={16} /><span>{t("library.maintenance")}</span><ChevronDown size={14} /></summary><div><HealthStrip /><VectorBuildCard /></div></details>
    {editor && <SkillEditor key={editor.skill?.name ?? "new"} skill={editor.skill} onClose={() => setEditor(null)}
      onSaved={(name) => { invalidate(name); setEditor(null); select(name, true); }} />}
  </div>;
}
