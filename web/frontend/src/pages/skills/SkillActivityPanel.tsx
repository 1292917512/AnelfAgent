import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Activity } from "lucide-react";
import { useActivityStore } from "@/stores/activity-store";
import { formatRelativeTimestamp } from "@/lib/format";
import { SkillEvidence } from "@/components/common/SkillEvidence";
import { QueryError } from "@/components/common/AsyncState";

export function SkillActivityPanel() {
  const { t } = useTranslation("skills");
  const [showMore, setShowMore] = useState(false);
  const runs = useActivityStore((state) => state.runs);
  const error = useActivityStore((state) => state.error);
  const loading = useActivityStore((state) => state.loading);
  const load = useActivityStore((state) => state.load);
  useEffect(() => { if (!useActivityStore.getState().loaded) void load(); }, [load]);
  const recent = useMemo(() => [...runs].reverse().flatMap((run) => {
    const skills = run.skills ?? [];
    return skills.length ? [{ run, skills }] : [];
  }).slice(0, 4), [runs]);
  return <section className="skills-activity" aria-label={t("recent.title")}>
    <div className="skills-section-heading"><Activity size={16} /><h2>{t("recent.title")}</h2><span>{t("recent.memoryOnly")}</span></div>
    <p className="skills-caption">{t("evidence.hint")}</p>
    {error ? <QueryError compact error={error} retry={() => void load()} /> : !recent.length && <p className="skills-caption py-3">{t(loading ? "recent.loading" : "recent.empty")}</p>}
    {(showMore ? recent : recent.slice(0, 1)).map(({ run, skills }) => <div className="skills-recent-run" key={run.id}>
      <div className="skills-recent-source"><span className={run.status === "running" ? "text-accent" : "text-muted"}>{t(run.status === "running" ? "recent.running" : "recent.ended")}</span><span>{run.source.channel || t("recent.system")}{run.source.target && ` · ${run.source.target}`}{run.actor && ` · ${run.actor}`}</span><time>{formatRelativeTimestamp(run.started_at)}</time></div>
      <p className="skills-recent-title">{run.label || t("recent.system")}</p>
      <SkillEvidence skills={skills} compact />
    </div>)}
    {recent.length > 1 && <button className="mt-2 min-h-9 text-xs text-accent" aria-expanded={showMore} onClick={() => setShowMore(!showMore)}>{t(showMore ? "recent.less" : "recent.more", { count: recent.length - 1 })}</button>}
  </section>;
}
