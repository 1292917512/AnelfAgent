import { useId } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ExternalLink, Package, Pencil } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, Switch } from "@/components/ui";
import type { ToolGroup, ToolItem } from "@/lib/types";

export function ToolGroupCard({ group, isOpen, busy, onToggle, onToggleGroup, onToggleTool, onEditTool, onOpenEntity }: {
  group: ToolGroup; isOpen: boolean; busy: boolean; onToggle: () => void; onToggleGroup: () => void;
  onToggleTool: (name: string) => void; onEditTool: (tool: ToolItem) => void; onOpenEntity: (group: string) => void;
}) {
  const { t } = useTranslation("tools");
  const contentId = useId();
  const title = t(`groups.${group.group}`, { defaultValue: group.group });
  return <section className="overflow-hidden rounded-xl border border-border bg-card">
    <div className="tool-group-header">
      <button type="button" aria-expanded={isOpen} aria-controls={contentId} onClick={onToggle} className="flex min-w-0 flex-1 items-center gap-3 py-3 text-left">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-elevated text-accent"><Package size={17} /></span>
        <span className="min-w-0 flex-1"><span className="block break-words text-sm font-semibold text-heading">{title}</span>
          <span className="mt-1 block text-xs text-muted">{t("enabledCount", { enabled: group.enabled_count, total: group.total_count })}</span></span>
        <ChevronDown size={16} className={cn("shrink-0 text-muted transition-transform", isOpen && "rotate-180")} />
      </button>
      <div className="flex shrink-0 items-center gap-2">
        <Button variant="ghost" size="icon" title={`${t("openEntity")}: ${title}`} onClick={() => onOpenEntity(group.group)}><ExternalLink size={16} /></Button>
        <Switch label={`${group.all_enabled ? t("disableGroup") : t("enableGroup")}: ${title}`} checked={group.all_enabled} disabled={busy} onChange={onToggleGroup} />
      </div>
    </div>
    {isOpen && <div id={contentId} className="border-t border-border">
      {group.description && <p className="border-b border-border bg-panel px-4 py-3 text-sm leading-relaxed text-muted">{group.description}</p>}
      {group.tools.map((tool) => <div key={tool.name} className="tool-item border-b border-border last:border-b-0">
        <div className="min-w-0 flex-1"><h3 className="break-all font-mono text-sm font-medium text-heading">{tool.name}</h3>
          {tool.description && <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-sm leading-relaxed text-muted" title={tool.description}>{tool.description}</p>}
          <div className="mt-2 flex flex-wrap gap-1.5">{tool.tags.map((tag) => <span key={tag} className="max-w-full break-all rounded-md bg-elevated px-2 py-1 text-xs text-muted">{tag}</span>)}</div>
        </div>
        <div className="tool-item-actions"><Button variant="ghost" size="icon" disabled={busy} title={`${t("editProperties")}: ${tool.name}`} onClick={() => onEditTool(tool)}><Pencil size={15} /></Button>
          <Switch checked={tool.enabled} label={`${tool.enabled ? t("disable") : t("enable")}: ${tool.name}`} disabled={busy} onChange={() => onToggleTool(tool.name)} /></div>
      </div>)}
    </div>}
  </section>;
}
