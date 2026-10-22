import { useState } from "react";
import { Link } from "react-router-dom";
import { Link2, ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { SkillMCPStatus } from "@/lib/types";
import { Modal } from "@/components/ui";

export function SkillMCPState({ state }: { state: SkillMCPStatus }) {
  const { t } = useTranslation("skills");
  if (state.configured !== true) return <span className="text-xs text-warn">{t(state.configured === null ? "mcp.unknown" : "mcp.missing")}</span>;
  return <span className="flex flex-wrap gap-x-2 gap-y-1 text-xs text-muted">
    <span>{t(state.enabled ? "mcp.enabled" : "mcp.disabled")}</span>
    <span className={state.connected ? "text-ok" : "text-warn"}>{t(state.connected ? "mcp.connected" : "mcp.disconnected")}</span>
    {state.activation && <span>{t(`mcp.activation.${state.activation}`)}</span>}
    {state.tool_count != null && <span>{t("mcp.tools", { count: state.tool_count })}</span>}
  </span>;
}

export function SkillMCPBadge({ states, skill }: { states: SkillMCPStatus[]; skill?: string }) {
  const { t } = useTranslation("skills");
  const [open, setOpen] = useState(false);
  if (!states.length) return null;
  const active = states.filter((state) => state.activation === "active").length;
  return <>
    <button type="button" className="inline-flex justify-self-start max-w-full items-center gap-1 rounded-md border border-border px-2 py-1 text-[11px] text-muted hover:bg-secondary" aria-haspopup="dialog" onClick={() => setOpen(true)}>
      <Link2 size={12} className="shrink-0" /><span>{t("mcp.badge", { count: states.length, active })}</span>
    </button>
    <Modal open={open} onClose={() => setOpen(false)} title={t("mcp.snapshotTitle")} width="max-w-lg">
      <p className="mb-3 text-xs text-muted">{t("mcp.snapshotHint")}</p>
      <div className="space-y-2">{states.map((state) => <div key={state.name} className="rounded-lg border border-border p-3"><p className="mb-1 break-all text-sm font-medium">{state.name}</p><SkillMCPState state={state} /></div>)}</div>
      <Link to={skill ? `/skills?skill=${encodeURIComponent(skill)}` : "/mcp"} className="mt-4 inline-flex items-center gap-1 text-sm text-accent" onClick={() => setOpen(false)}>{t(skill ? "mcp.editSkill" : "mcp.configure")}<ArrowUpRight size={14} /></Link>
    </Modal>
  </>;
}
