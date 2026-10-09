import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { GraduationCap, Plus } from "lucide-react";
import { skillsApi } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { Button, EmptyState, Input } from "@/components/ui";
import { AsyncState } from "@/components/common/AsyncState";
import { VectorBuildCard, HealthStrip } from "./SkillsHealth";
import { SkillCard } from "./SkillCard";
import { SkillEditor } from "./SkillEditor";

export function SkillsPanel() {
  const { t } = useTranslation("skills");
  const client = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [editor, setEditor] = useState<{ skill: SkillItem | null } | null>(null);
  const query = useQuery({
    queryKey: ["skills", showArchived], queryFn: () => skillsApi.list(showArchived).then((r) => r.data), throwOnError: false,
  });
  const filter = keyword.trim().toLocaleLowerCase();
  const skills = (query.data ?? []).filter((skill) => !filter ||
    [skill.name, skill.description, ...skill.trigger_patterns].some((text) => text.toLocaleLowerCase().includes(filter)));
  const invalidate = (name?: string) => {
    void client.invalidateQueries({ queryKey: ["skills"] });
    if (name) void client.invalidateQueries({ queryKey: ["skill", name] });
  };
  return <div className="space-y-4">
    <VectorBuildCard /><HealthStrip />
    <div className="flex flex-wrap items-center gap-3">
      <Input aria-label={t("searchPlaceholder")} value={keyword} onChange={(event) => setKeyword(event.target.value)}
        placeholder={t("searchPlaceholder")} className="min-w-40 flex-1 sm:max-w-sm" />
      <label className="flex cursor-pointer items-center gap-2 text-sm text-muted">
        <input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />{t("showArchived")}
      </label>
      <Button variant="primary" className="ml-auto" onClick={() => setEditor({ skill: null })}><Plus size={16} />{t("createNew")}</Button>
    </div>
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      {!skills.length && <EmptyState icon={GraduationCap} title={filter ? t("common:noData") : t("empty")} />}
      <div className="space-y-3">{skills.map((skill) => <SkillCard key={skill.name} skill={skill} expanded={selected === skill.name}
        onToggle={() => setSelected(selected === skill.name ? null : skill.name)} onEdit={(detail) => setEditor({ skill: detail })}
        onChanged={() => invalidate(skill.name)} onDeleted={() => { if (selected === skill.name) setSelected(null); invalidate(skill.name); }} />)}</div>
    </AsyncState>
    {editor && <SkillEditor key={editor.skill?.name ?? "new"} skill={editor.skill} onClose={() => setEditor(null)}
      onSaved={(name) => { invalidate(name); setEditor(null); setSelected(name); }} />}
  </div>;
}
