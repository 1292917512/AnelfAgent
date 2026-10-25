import { useId } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, ExternalLink, Package, Pencil, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, Switch } from "@/components/ui";
import type { ToolGroup, ToolItem } from "@/lib/types";
import { ToolOriginBadge } from "./ToolSources";
import { TOOL_ORIGINS } from "./catalog";

export function ToolGroupCard({ group, isOpen, busy, reloading, onReload, onToggle, onToggleGroup, onToggleTool, onEditTool, onOpenEntity }: {
  group: ToolGroup; isOpen: boolean; busy: boolean; onToggle: () => void; onToggleGroup: () => void;
  reloading: boolean; onReload: (module: string) => void;
  onToggleTool: (name: string) => void; onEditTool: (tool: ToolItem) => void; onOpenEntity: (group: string) => void;
}) {
  const { t } = useTranslation("tools");
  const contentId = useId();
  const title = t(`groups.${group.group}`, { defaultValue: group.group });
  const origins = TOOL_ORIGINS.filter((kind) => group.tools.some((tool) => tool.origin?.kind === kind));
  const reloadModule = origins.length === 1 && origins[0] === "core" ? null : group.reload_module;
  return <section className="overflow-hidden rounded-xl border border-border bg-card">
    <div className="tool-group-header">
      <button type="button" aria-expanded={isOpen} aria-controls={contentId} onClick={onToggle} className="flex min-w-0 flex-1 items-center gap-3 py-3 text-left">
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-elevated text-accent"><Package size={17} /></span>
        <span className="min-w-0 flex-1"><span className="block break-words text-sm font-semibold text-heading">{title}</span>
          <span className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">{origins.map((kind) => <ToolOriginBadge key={kind} kind={kind} />)}
            {group.tools.length < group.total_count && <span>{t("groupMatches", { count: group.tools.length, total: group.total_count })}</span>}
            <span>{t("enabledCount", { enabled: group.enabled_count, total: group.total_count })}</span></span></span>
        <ChevronDown size={16} className={cn("shrink-0 text-muted transition-transform", isOpen && "rotate-180")} />
      </button>
      <div className="flex shrink-0 items-center gap-2">
        {reloadModule && <Button variant="ghost" size="icon" title={t("reloadEntity", { name: reloadModule })} disabled={busy} loading={reloading}
          onClick={() => onReload(reloadModule)}>{!reloading && <RefreshCw size={16} />}</Button>}
        <Button variant="ghost" size="icon" title={`${t("openEntity")}: ${title}`} onClick={() => onOpenEntity(group.group)}><ExternalLink size={16} /></Button>
        <span title={t("groupToggleHint")}><Switch label={`${group.all_enabled ? t("disableGroup") : t("enableGroup")}: ${title}`} checked={group.all_enabled} disabled={busy} onChange={onToggleGroup} /></span>
      </div>
    </div>
    {isOpen && <div id={contentId} className="border-t border-border">
      {group.description && <p className="border-b border-border bg-panel px-4 py-3 text-sm leading-relaxed text-muted">{group.description}</p>}
      {group.tools.map((tool) => <div key={tool.name} className="tool-item border-b border-border last:border-b-0">
        <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="break-all font-mono text-sm font-medium text-heading">{tool.name}</h3>
          {origins.length > 1 && tool.origin && <ToolOriginBadge kind={tool.origin.kind} />}</div>
          {tool.description && <p className="mt-1.5 line-clamp-3 whitespace-pre-wrap text-sm leading-relaxed text-muted" title={tool.description}>{tool.description}</p>}
          <div className="mt-2 flex flex-wrap gap-1.5">{tool.tags.map((tag) => <span key={tag} className="max-w-full break-all rounded-md bg-elevated px-2 py-1 text-xs text-muted">{tag}</span>)}</div>
        </div>
        <div className="tool-item-actions"><Button variant="ghost" size="icon" disabled={busy} title={`${t("editProperties")}: ${tool.name}`} onClick={() => onEditTool(tool)}><Pencil size={15} /></Button>
          <Switch checked={tool.enabled} label={`${tool.enabled ? t("disable") : t("enable")}: ${tool.name}`} disabled={busy} onChange={() => onToggleTool(tool.name)} /></div>
      </div>)}
    </div>}
  </section>;
}
