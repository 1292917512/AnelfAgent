import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Crosshair, Database, List, ListTree, MoreHorizontal, Power, Workflow, Wrench } from "lucide-react";
import * as Popover from "@radix-ui/react-popover";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

export type ThinkingViewMode = "flow" | "timeline";
interface Props {
  isMobile: boolean; sessionsHidden: boolean; onShowSessions: () => void; enabled: boolean; onToggle: () => void;
  busy: boolean; connected: boolean; view: ThinkingViewMode; onViewChange: (view: ThinkingViewMode) => void;
  onShowTools: () => void; onShowProviders: () => void; autoFollow: boolean; onToggleAutoFollow: () => void;
}

export function ThinkingToolbar(props: Props) {
  const { t } = useTranslation("thinking");
  return <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-border bg-panel px-3 py-2">
    {props.sessionsHidden && <Button size="icon" variant="ghost" title={t("sessionList")} onClick={props.onShowSessions}><List size={16} /></Button>}
    <Button size={props.isMobile ? "icon" : "sm"} loading={props.busy} aria-pressed={props.enabled} onClick={props.onToggle}
      title={t(props.enabled ? "tracking" : "startTracking")} aria-label={t(props.enabled ? "tracking" : "startTracking")}
      className={props.enabled ? "border-accent/30 bg-accent-subtle text-accent" : ""}>
      {!props.busy && <Power size={14} />}{!props.isMobile && t(props.enabled ? "tracking" : "startTracking")}
    </Button>
    <span className="inline-flex items-center gap-1.5 text-xs text-muted" role="status">
      <span className={cn("size-1.5 rounded-full", !props.enabled ? "bg-muted" : props.connected ? "bg-ok" : "bg-warn")} />
      <span className="sr-only lg:not-sr-only">{t(!props.enabled ? "disabled" : props.connected ? "streamConnected" : "reconnecting")}</span>
    </span>
    <div className="ml-auto flex items-center rounded-lg border border-border p-0.5">
      {(["timeline", "flow"] as const).map((view) => <Button key={view} variant="ghost" size="sm"
        aria-label={t(`views.${view}`)} aria-pressed={props.view === view} title={t(`views.${view}`)}
        onClick={() => props.onViewChange(view)} className={cn("px-2", props.view === view && "bg-accent-subtle text-accent")}>
        {view === "timeline" ? <ListTree size={14} /> : <Workflow size={14} />}
        <span className="hidden sm:inline">{t(`views.${view}`)}</span>
      </Button>)}
    </div>
    <Button size="icon" variant="ghost" aria-pressed={props.autoFollow} title={t("autoFollow")} onClick={props.onToggleAutoFollow}
      className={props.autoFollow ? "bg-accent-subtle text-accent" : ""}><Crosshair size={16} /></Button>
    {props.isMobile ? <Popover.Root><Popover.Trigger asChild><Button size="icon" variant="ghost" aria-label={t("nav:more")}><MoreHorizontal size={18} /></Button></Popover.Trigger>
      <Popover.Portal><Popover.Content align="end" sideOffset={8} className="z-[110] w-64 rounded-2xl border border-border bg-card p-2 shadow-lg">
        <Popover.Close asChild><button className="menu-action" onClick={props.onShowTools}><Wrench size={16} />{t("toolsPanel")}</button></Popover.Close>
        <Popover.Close asChild><button className="menu-action" onClick={props.onShowProviders}><Database size={16} />{t("contextProviders.title")}</button></Popover.Close>
        <Link to="/context" className="menu-action text-accent"><Database size={16} />{t("contextTab")}</Link>
      </Popover.Content></Popover.Portal></Popover.Root> : <>
      <Button size="icon" variant="ghost" title={t("toolsPanel")} onClick={props.onShowTools}><Wrench size={16} /></Button>
      <Button size="icon" variant="ghost" title={t("contextProviders.title")} onClick={props.onShowProviders}><Database size={16} /></Button>
      <Link to="/context" className="rounded-lg px-2 py-2 text-xs font-medium text-accent hover:bg-hover">{t("contextTab")}</Link>
    </>}
  </div>;
}
