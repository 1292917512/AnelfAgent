import { BookOpen, Search, Sparkles, ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ActivitySkill } from "@/lib/types/activity";

/** 以可验证的注入和读取事实展示技能，链接到技能库详情。 */
export function SkillEvidence({ skills, compact = false }: { skills: ActivitySkill[]; compact?: boolean }) {
  const { t } = useTranslation("skills");
  if (!skills.length) return null;
  return <div className={compact ? "skill-evidence is-compact" : "skill-evidence"} aria-label={t("evidence.title")}>
    {skills.map((skill) => <Link key={skill.name} to={`/skills?skill=${encodeURIComponent(skill.name)}`}
      className="skill-evidence-item" data-mode={skill.mode} title={skill.description || skill.name}>
      <span className="skill-evidence-icon">{skill.mode === "read" ? <BookOpen size={15} /> : skill.mode === "explicit" ? <Sparkles size={15} /> : <Search size={15} />}</span>
      <span className="skill-evidence-body"><strong>{skill.name}</strong>{!compact && skill.description && <span>{skill.description}</span>}
        <span className="skill-evidence-meta"><b>{t(`evidence.${skill.mode}`)}</b>
          {skill.score != null && <span>{t("evidence.score", { score: skill.score.toFixed(2) })}</span>}
          {skill.state === "archived" && <span>{t("stateArchived")}</span>}
          {skill.truncated && <span>{t("evidence.partial")}</span>}
        </span>
      </span><ArrowUpRight size={13} className="shrink-0 text-muted" />
    </Link>)}
  </div>;
}
