import { useTranslation } from "react-i18next";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { ArrowDown, ArrowUp, GripVertical, Globe, Terminal, AlertCircle } from "lucide-react";
import type { MCPServer } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, Button } from "@/components/ui";
import { cn } from "@/lib/utils";
import { ToolsSection } from "./ToolsSection";
import { ServerActions } from "./ServerActions";

export function ServerCard({ server, oauth, position, total, disabled, sortable, onMove, onEdit, onDelete }: {
  server: MCPServer; oauth?: { authorized: boolean; pending_url: string };
  position: number; total: number; disabled: boolean; sortable: boolean;
  onMove: (offset: number) => void; onEdit: (server: MCPServer) => void; onDelete: (server: MCPServer) => void;
}) {
  const { t } = useTranslation("mcp");
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({ id: server.name, disabled: !sortable });
  const Icon = server.transport === "stdio" ? Terminal : Globe;
  const state = server.connected ? "ok" : server.enabled ? "warn" : "offline";
  const label = server.connected ? server.channel_only ? "channelOnlyStatus" : "connectedStatus" : server.enabled ? "disconnectedStatus" : "disabledStatus";
  return <section ref={setNodeRef} aria-label={server.name} style={{ transform: CSS.Transform.toString(transform), transition }}
    className={cn("relative min-w-0 rounded-xl border border-border bg-card p-3 sm:p-4", isDragging && "z-10 shadow-xl ring-2 ring-accent")}>
    <div className="flex items-start gap-2 sm:gap-3">
      <button ref={setActivatorNodeRef} {...attributes} {...listeners} disabled={!sortable}
        aria-label={t("reorder.drag", { name: server.name })} title={t("reorder.drag", { name: server.name })}
        className="shrink-0 touch-none rounded-lg p-2 text-muted hover:bg-secondary disabled:opacity-30 cursor-grab active:cursor-grabbing">
        <GripVertical size={16} />
      </button>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="w-5 shrink-0 text-right text-xs tabular-nums text-muted">{position + 1}</span>
          <Icon size={16} className="shrink-0 text-accent" />
          <h2 className="min-w-0 break-all text-sm font-semibold text-heading">{server.name}</h2>
          <span className="inline-flex items-center gap-1.5 text-xs text-muted"><StatusDot status={state} />{t(label)}</span>
          <Badge variant="neutral">{server.transport}</Badge>
          {server.plugin && <Badge variant="info">{t("pluginBadge", { plugin: server.plugin })}</Badge>}
          {server.url && <p className="min-w-0 flex-1 truncate font-mono text-xs text-muted" title={server.url}>{server.url}</p>}
          <div className="ms-auto flex min-w-0 flex-wrap items-center justify-end gap-2">
            {server.connected && !server.channel_only && <ToolsSection serverName={server.name} toolCount={server.tool_count} />}
            <span className="text-[11px] text-muted">{t(server.sleeping ? "sleepingBadge" : "awakeBadge")}</span>
            {oauth?.pending_url ? <a href={oauth.pending_url} target="_blank" rel="noreferrer" className="text-xs text-accent hover:underline">{t("oauthPending")}</a>
              : oauth?.authorized && <Badge variant="info">{t("oauthAuthorized")}</Badge>}
            <div className="flex shrink-0 items-center gap-2">
              <div className="flex items-center border-r border-border pr-2">
                <Button size="icon" variant="ghost" disabled={!sortable || position === 0} onClick={() => onMove(-1)}
                  title={t("reorder.up")} aria-label={`${t("reorder.up")} · ${server.name}`}><ArrowUp size={14} /></Button>
                <Button size="icon" variant="ghost" disabled={!sortable || position === total - 1} onClick={() => onMove(1)}
                  title={t("reorder.down")} aria-label={`${t("reorder.down")} · ${server.name}`}><ArrowDown size={14} /></Button>
              </div>
              <ServerActions server={server} oauth={oauth} disabled={disabled} onEdit={onEdit} onDelete={onDelete} />
            </div>
          </div>
        </div>
        {!server.connected && server.last_error && <details className="rounded-lg bg-danger-subtle px-3 py-2 text-xs text-danger">
          <summary className="cursor-pointer"><AlertCircle size={12} className="mr-1 inline" />{t("lastError")}</summary>
          <p className="mt-2 whitespace-pre-wrap break-all">{server.last_error}</p>
        </details>}
      </div>
    </div>
  </section>;
}
