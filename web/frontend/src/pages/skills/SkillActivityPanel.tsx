import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Activity, ArrowUpRight, BookOpen, GraduationCap, Loader2, Pencil, Search, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useActivityStore } from "@/stores/activity-store";
import { formatRelativeTimestamp } from "@/lib/format";
import { QueryError } from "@/components/common/AsyncState";
import { skillsApi } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { skillParticipation } from "./skill-activity";

export function SkillActivityPanel({ onEdit }: { onEdit: (skill: SkillItem) => void }) {
  const { t } = useTranslation("skills");
  const [showMore, setShowMore] = useState(false);
  const runs = useActivityStore((state) => state.runs);
  const error = useActivityStore((state) => state.error);
  const loading = useActivityStore((state) => state.loading);
  const load = useActivityStore((state) => state.load);
  const client = useQueryClient();
  const edit = useMutation({ mutationFn: (name: string) => client.fetchQuery({ queryKey: ["skill", name], queryFn: () => skillsApi.get(name).then((r) => r.data) }), onSuccess: onEdit });
  useEffect(() => { if (!useActivityStore.getState().loaded) void load(); }, [load]);
  const recent = useMemo(() => skillParticipation(runs), [runs]);
  return <section className="skills-activity" aria-label={t("recent.title")}>
    <div className="skills-section-heading"><Activity size={16} /><h2>{t("recent.title")}</h2><span>{t("recent.memoryOnly")}</span></div>
    <p className="skills-caption" title={t("evidence.hint")}>{t("recent.hint")}</p>
    {error ? <QueryError compact error={error} retry={() => void load()} /> : !recent.length && <p className="skills-caption py-3">{t(loading ? "recent.loading" : "recent.empty")}</p>}
    {edit.error && <QueryError compact error={edit.error} />}
    <ul className="skills-recent-list">{(showMore ? recent : recent.slice(0, 6)).map(({ skill, count, modes, latest }) => <li key={skill.name}>
      <GraduationCap size={17} className="shrink-0 text-accent" />
      <div className="skills-recent-main">
        <Link to={`/skills?skill=${encodeURIComponent(skill.name)}`} title={skill.description} className="skills-recent-name">{skill.name}<ArrowUpRight size={12} /></Link>
        <div className="skills-recent-meta">
          {(["read", "explicit", "candidate"] as const).map((mode) => {
            const Icon = mode === "read" ? BookOpen : mode === "explicit" ? Sparkles : Search;
            return modes[mode] > 0 && <span key={mode} title={t(`evidence.${mode}`)}><Icon size={12} />{t(`evidence.${mode}`)} {modes[mode]}</span>;
          })}
          <span title={latest.label}>{latest.source.channel || t("recent.system")}{latest.source.target && ` · ${latest.source.target}`}</span>
          <time dateTime={new Date(latest.started_at * 1000).toISOString()}>{formatRelativeTimestamp(latest.started_at)}</time>
        </div>
      </div>
      <span className="skills-recent-count" title={t("recent.count", { count })}>{count}<span>{t("recent.runs")}</span></span>
      <button className="skills-recent-edit" aria-label={t("recent.edit", { name: skill.name })} title={t("edit")} disabled={edit.isPending} onClick={() => edit.mutate(skill.name)}>
        {edit.isPending && edit.variables === skill.name ? <Loader2 size={15} className="animate-spin" /> : <Pencil size={15} />}
      </button>
    </li>)}</ul>
    {recent.length > 6 && <button className="mt-2 min-h-9 text-xs text-accent" aria-expanded={showMore} onClick={() => setShowMore(!showMore)}>{t(showMore ? "recent.less" : "recent.more", { count: recent.length - 6 })}</button>}
  </section>;
}
