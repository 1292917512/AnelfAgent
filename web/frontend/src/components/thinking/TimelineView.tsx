import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronDown, Search, SlidersHorizontal } from "lucide-react";
import type { ThinkingSession } from "@/lib/types";
import { cn } from "@/lib/utils";
import { TraceNodeRow } from "./TraceNodeRow";
import { nodeStage, nodeSummary, nodeTitle, traceGroups, type TraceStage } from "./trace-model";

interface Props {
  session: ThinkingSession; selectedNodeId: string | null;
  autoFollow: boolean; onSelect: (id: string) => void; compact?: boolean;
}
type Filter = "all" | "issues" | TraceStage;
const FILTERS: Filter[] = ["all", "issues", "prepare", "reason", "act", "finish"];

/** Groups the recorded execution into rounds with searchable events and explicit status filters. */
export function TimelineView({ session, selectedNodeId, autoFollow, onSelect, compact = false }: Props) {
  const { t } = useTranslation("thinking");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [details, setDetails] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const scrollRef = useRef<HTMLDivElement>(null);
  const groups = useMemo(() => traceGroups(session.nodes), [session.nodes]);
  const terms = query.trim().toLocaleLowerCase();
  const visibleGroups = groups.map((group) => ({
    ...group, visible: group.nodes.filter((node) => {
      if (node.id === selectedNodeId) return true;
      if (filter === "issues" && node.status !== "error" && node.status !== "warning") return false;
      if (filter !== "all" && filter !== "issues" && nodeStage(node) !== filter) return false;
      if (terms && ![nodeTitle(node, t), nodeSummary(node, t), node.type, node.label].join(" ").toLocaleLowerCase().includes(terms)) return false;
      if (!terms && filter === "all" && !details && ["phase_change", "session_start", "reply_round"].includes(node.type)) return false;
      return true;
    }),
  })).filter((group) => group.visible.length);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    if (selectedNodeId) {
      const row = Array.from(element.querySelectorAll<HTMLElement>("[data-trace-node]"))
        .find((item) => item.dataset.traceNode === selectedNodeId);
      row?.scrollIntoView({ block: "nearest" });
    } else if (autoFollow && !session.ended) element.scrollTop = element.scrollHeight;
  }, [selectedNodeId, session.nodes.length, session.ended, autoFollow]);

  return <div className="flex h-full min-h-0 flex-col">
    <div className="shrink-0 space-y-2 border-b border-border bg-panel p-3">
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-2.5 text-muted" />
          <input aria-label={t("searchNodes")} placeholder={t("searchNodes")} value={query} onChange={(event) => setQuery(event.target.value)}
            className="h-9 w-full rounded-lg border border-input bg-card pl-8 pr-3 text-xs outline-none focus:border-accent" />
        </div>
        <button type="button" aria-label={t("showAllEvents")} title={t("showAllEvents")} aria-pressed={details}
          onClick={() => setDetails(!details)} className={cn("rounded-lg border p-2.5", details ? "border-accent text-accent" : "border-border text-muted")}>
          <SlidersHorizontal size={14} />
        </button>
      </div>
      <div className="flex gap-1 overflow-x-auto whitespace-nowrap pb-1" aria-label={t("filterTypes")}>
        {FILTERS.map((key) => <button type="button" key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}
          className={cn("rounded-md px-2 py-1 text-[11px]", filter === key ? "bg-accent-subtle font-medium text-accent" : "text-muted hover:bg-hover")}>
          {t(key === "all" || key === "issues" ? `filters.${key}` : `stages.${key}`)}
        </button>)}
      </div>
    </div>
    <div ref={scrollRef} className="trace-timeline min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 md:p-5">
      {visibleGroups.length === 0 && <div className="px-4 py-10 text-center text-sm text-muted">
        <p>{t(session.nodes.length ? "noMatchingNodes" : "waitingForActivity")}</p>
        {(terms || filter !== "all") && <button className="mt-3 text-accent" onClick={() => { setQuery(""); setFilter("all"); }}>{t("resetFilters")}</button>}
      </div>}
      <div className="trace-rounds mx-auto max-w-4xl">
        {visibleGroups.map((group, index) => {
          const hasIssue = group.nodes.some((node) => node.status === "error" || node.status === "warning");
          const containsSelected = group.nodes.some((node) => node.id === selectedNodeId);
          const isOpen = containsSelected || (expanded[group.id] ?? (index === visibleGroups.length - 1 || hasIssue || !!terms || filter !== "all" || !group.anchor));
          return <section key={group.id} className="trace-round bg-card p-2">
            <button type="button" aria-expanded={isOpen} onClick={() => setExpanded((value) => ({ ...value, [group.id]: !isOpen }))}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-muted hover:text-foreground">
              <ChevronDown size={14} className={cn("transition-transform", !isOpen && "-rotate-90")} />
              <span className="font-medium text-heading">{group.anchor ? nodeTitle(group.anchor, t) : t("activityGroup")}</span>
              <span>{t("nNodes", { count: group.visible.length })}</span>
              {hasIssue && <span className="ml-auto text-warn">{t("filters.issues")}</span>}
            </button>
            {isOpen && <div className={cn("trace-events relative", compact && "text-xs")}>
              {group.visible.map((node) => <TraceNodeRow key={node.id} node={node} compact={compact}
                selected={selectedNodeId === node.id} onSelect={() => onSelect(node.id)} />)}
            </div>}
          </section>;
        })}
      </div>
    </div>
  </div>;
}
