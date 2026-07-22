import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { CheckCircle2, ChevronRight, Clock, AlertCircle, Loader2 } from "lucide-react";
import type { ThinkingSession, TraceNode } from "@/stores/thinking-store";
import { TYPE_STYLES } from "./TraceNode";
import { NodeDetail } from "./NodeDetail";
import { cn } from "@/lib/utils";

interface Props {
  session: ThinkingSession;
  selectedNodeId: string | null;
  autoFollow: boolean;
  onSelect: (nodeId: string | null) => void;
}

const STATUS_ICON: Record<string, React.ElementType> = {
  running: Loader2,
  completed: CheckCircle2,
  error: AlertCircle,
  pending: Clock,
};

const STATUS_COLOR: Record<string, string> = {
  running: "text-accent animate-spin",
  completed: "text-ok",
  error: "text-danger",
  pending: "text-muted",
};

/** 时间线视图：按时间顺序平铺节点，层级缩进，点击展开详情 */
export function TimelineView({ session, selectedNodeId, autoFollow, onSelect }: Props) {
  const { t } = useTranslation("thinking");
  const [typeFilter, setTypeFilter] = useState<Set<string> | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const depthMap = useMemo(() => {
    const byId = new Map(session.nodes.map((n) => [n.id, n]));
    const map = new Map<string, number>();
    const depthOf = (n: TraceNode, guard: number): number => {
      const cached = map.get(n.id);
      if (cached !== undefined) return cached;
      let d = 0;
      if (n.parent_id && guard < 32) {
        const parent = byId.get(n.parent_id);
        if (parent) d = depthOf(parent, guard + 1) + 1;
      }
      map.set(n.id, d);
      return d;
    };
    for (const n of session.nodes) depthOf(n, 0);
    return map;
  }, [session.nodes]);

  const typeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of session.nodes) counts.set(n.type, (counts.get(n.type) ?? 0) + 1);
    return counts;
  }, [session.nodes]);

  const visible = useMemo(() => {
    if (!typeFilter) return session.nodes;
    return session.nodes.filter((n) => typeFilter.has(n.type));
  }, [session.nodes, typeFilter]);

  useEffect(() => {
    if (autoFollow && scrollRef.current) {
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      });
    }
  }, [session.nodes.length, autoFollow]);

  const toggleType = (type: string) => {
    setTypeFilter((prev) => {
      const next = new Set(prev ?? typeCounts.keys());
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full">
      {/* 类型过滤 */}
      <div className="flex items-center gap-1.5 px-3 md:px-4 py-2 border-b border-border overflow-x-auto no-scrollbar">
        <span className="text-[10px] text-muted shrink-0">{t("filterTypes")}</span>
        {[...typeCounts.entries()].map(([type, count]) => {
          const active = !typeFilter || typeFilter.has(type);
          const style = TYPE_STYLES[type];
          const Icon = style?.icon;
          return (
            <button
              key={type}
              onClick={() => toggleType(type)}
              className={cn(
                "flex items-center gap-1 px-2 py-1 rounded-full border text-[10px] font-medium whitespace-nowrap transition-all",
                active
                  ? "border-border-strong text-foreground bg-card"
                  : "border-border text-muted opacity-40",
              )}
            >
              {Icon && <Icon size={10} className={style.accent} />}
              {t(`nodeTypes.${type}`, { defaultValue: type })}
              <span className="opacity-60">{count}</span>
            </button>
          );
        })}
      </div>

      {/* 节点列表 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        {visible.length === 0 && (
          <p className="text-sm text-muted text-center py-10">{t("waitingForActivity")}</p>
        )}
        {visible.map((n) => {
          const depth = depthMap.get(n.id) ?? 0;
          const style = TYPE_STYLES[n.type] ?? TYPE_STYLES.phase_change!;
          const Icon = style.icon;
          const StatusIcon = STATUS_ICON[n.status] ?? Clock;
          const expanded = selectedNodeId === n.id;
          const ts = new Date(n.timestamp * 1000);
          return (
            <div key={n.id} className="border-b border-border/50">
              <button
                onClick={() => onSelect(expanded ? null : n.id)}
                className={cn(
                  "w-full flex items-center gap-2 px-3 md:px-4 py-2 text-left transition-colors",
                  expanded ? "bg-accent-subtle/40" : "hover:bg-hover",
                )}
                style={{ paddingLeft: `${(depth * 18) + 12}px` }}
              >
                <ChevronRight
                  size={12}
                  className={cn("shrink-0 text-muted transition-transform", expanded && "rotate-90")}
                />
                <span className={cn("flex items-center justify-center w-5 h-5 rounded-sm border shrink-0", style.bg, style.border)}>
                  <Icon size={11} className={style.accent} />
                </span>
                <span className="text-xs text-foreground truncate flex-1 min-w-0">{n.label}</span>
                {n.duration_ms != null && (
                  <span className="text-[10px] text-muted font-mono shrink-0">
                    {n.duration_ms >= 1000 ? `${(n.duration_ms / 1000).toFixed(1)}s` : `${Math.round(n.duration_ms)}ms`}
                  </span>
                )}
                <span className="text-[10px] text-muted font-mono shrink-0 hidden sm:inline">
                  {ts.toLocaleTimeString()}
                </span>
                <StatusIcon size={12} className={cn("shrink-0", STATUS_COLOR[n.status])} />
              </button>
              {expanded && (
                <div className="border-t border-border bg-panel h-[55vh] max-h-[480px]">
                  <NodeDetail node={n} onClose={() => onSelect(null)} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
