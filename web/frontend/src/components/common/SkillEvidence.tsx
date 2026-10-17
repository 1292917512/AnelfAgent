import { BookOpen, GraduationCap, Search, ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ActivitySkill } from "@/lib/types/activity";
import { memo, useState } from "react";
import { SkillMCPBadge } from "@/components/skills/SkillMCPStatus";
import { Modal } from "@/components/ui";

/** 以可验证的注入和读取事实展示技能，链接到技能库详情。 */
export const SkillEvidence = memo(function SkillEvidence({ skills, compact = false, onSelect }: { skills: ActivitySkill[]; compact?: boolean; onSelect?: () => void }) {
  const { t } = useTranslation("skills");
  const [open, setOpen] = useState(false);
  if (!skills.length) return null;
  if (compact) return <>
    <button className="skill-evidence-summary" aria-label={t("evidence.title")} aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <GraduationCap size={14} className="shrink-0 text-accent" />
      <span className="skill-evidence-summary-body">
        <span className="skill-evidence-counts">{(["read", "candidate"] as const).map((mode) => {
          const count = skills.filter((skill) => skill.mode === mode).length;
          return count > 0 && <span key={mode} data-mode={mode}>{t(`evidence.${mode}`)} <b>{count}</b></span>;
        })}</span>
        <span className="skill-evidence-names">{skills.slice(0, 3).map((skill) => <span key={skill.name} title={skill.description} data-mode={skill.mode}>{skill.name}</span>)}{skills.length > 3 && <span>+{skills.length - 3}</span>}</span>
      </span><ArrowUpRight size={13} className="shrink-0 text-muted" />
    </button>
    {open && <Modal open title={t("evidence.title")} onClose={() => setOpen(false)} width="max-w-2xl"><p className="skills-caption">{t("evidence.hint")}</p><SkillEvidence skills={skills} onSelect={() => setOpen(false)} /></Modal>}
  </>;
  return <div className="skill-evidence" aria-label={t("evidence.title")}>
    {skills.map((skill) => <div key={skill.name} className="space-y-1"><Link to={`/skills?skill=${encodeURIComponent(skill.name)}`} onClick={onSelect}
      className="skill-evidence-item" data-mode={skill.mode} title={skill.description || skill.name}>
      <span className="skill-evidence-icon">{skill.mode === "read" ? <BookOpen size={15} /> : <Search size={15} />}</span>
      <span className="skill-evidence-body"><strong>{skill.name}</strong>{skill.description && <span>{skill.description}</span>}
        <span className="skill-evidence-meta"><b>{t(`evidence.${skill.mode}`)}</b>
          {skill.score != null && <span>{t("evidence.score", { score: skill.score.toFixed(2) })}</span>}
          {skill.state === "archived" && <span>{t("stateArchived")}</span>}
          {skill.truncated && <span>{t("evidence.partial")}</span>}
        </span>
      </span><ArrowUpRight size={13} className="shrink-0 text-muted" />
    </Link><SkillMCPBadge states={skill.mcp_status ?? []} skill={skill.name} /></div>)}
  </div>;
});
