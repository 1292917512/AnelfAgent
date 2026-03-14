import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Play,
  Square,
  Zap,
  Brain,
  Wrench,
  MessageSquare,
  Search,
  GitBranch,
  Layers,
  AlertCircle,
  CheckCircle2,
  Loader2,
  ScanEye,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { TFunction } from "i18next";

export interface TraceNodeData {
  label: string;
  nodeType: string;
  status: string;
  duration_ms: number | null;
  data: Record<string, unknown>;
  [key: string]: unknown;
}

const TYPE_STYLES: Record<string, { bg: string; border: string; icon: React.ElementType; accent: string }> = {
  session_start: {
    bg: "bg-[var(--ok-subtle)]",
    border: "border-[var(--ok)]",
    icon: Play,
    accent: "text-[var(--ok)]",
  },
  session_end: {
    bg: "bg-[var(--bg-muted)]",
    border: "border-[var(--muted)]",
    icon: Square,
    accent: "text-[var(--muted)]",
  },
  phase_change: {
    bg: "bg-[var(--accent-2-subtle)]",
    border: "border-[var(--accent-2)]",
    icon: Zap,
    accent: "text-[var(--accent-2)]",
  },
  situation: {
    bg: "bg-[var(--info)]/10",
    border: "border-[var(--info)]",
    icon: Search,
    accent: "text-[var(--info)]",
  },
  decision: {
    bg: "bg-[var(--warn-subtle)]",
    border: "border-[var(--warn)]",
    icon: GitBranch,
    accent: "text-[var(--warn)]",
  },
  context_build: {
    bg: "bg-[var(--bg-muted)]",
    border: "border-[var(--border-strong)]",
    icon: Layers,
    accent: "text-[var(--muted)]",
  },
  llm_call: {
    bg: "bg-purple-500/10",
    border: "border-purple-500",
    icon: Brain,
    accent: "text-purple-400",
  },
  tool_call: {
    bg: "bg-[var(--accent-subtle)]",
    border: "border-[var(--accent)]",
    icon: Wrench,
    accent: "text-[var(--accent)]",
  },
  reply_round: {
    bg: "bg-[var(--accent-2-subtle)]",
    border: "border-[var(--accent-2)]",
    icon: MessageSquare,
    accent: "text-[var(--accent-2)]",
  },
  introspection: {
    bg: "bg-pink-500/10",
    border: "border-pink-500",
    icon: ScanEye,
    accent: "text-pink-400",
  },
};

const STATUS_ICON: Record<string, React.ElementType> = {
  running: Loader2,
  completed: CheckCircle2,
  error: AlertCircle,
};

function getSubtitle(d: TraceNodeData, t: TFunction): string | null {
  const data = d.data;
  switch (d.nodeType) {
    case "llm_call": {
      const content = data.content_preview as string | undefined;
      const toolCalls = data.tool_calls as string[] | undefined;
      const hasReasoning = data.has_reasoning as boolean | undefined;
      const reasoningPreview = data.reasoning_preview as string | undefined;
      if (toolCalls && toolCalls.length > 0) return `→ ${toolCalls.join(", ")}`;
      if (content) return content.slice(0, 60) + (content.length > 60 ? "..." : "");
      if (hasReasoning && reasoningPreview) return `💭 ${reasoningPreview.slice(0, 50)}${reasoningPreview.length > 50 ? "..." : ""}`;
      if (hasReasoning) return t("hasReasoning");
      return null;
    }
    case "tool_call": {
      const args = data.arguments as Record<string, unknown> | string | undefined;
      const result = data.result_preview as string | undefined;
      if (result) return result.slice(0, 50) + (result.length > 50 ? "..." : "");
      if (typeof args === "string") return args.slice(0, 50);
      if (args && typeof args === "object") {
        const keys = Object.keys(args).slice(0, 3);
        return keys.map((k) => `${k}: ${String(args[k]).slice(0, 15)}`).join(", ");
      }
      return null;
    }
    case "decision": {
      const decisions = data.decisions as Array<{ type?: string; target?: string }> | undefined;
      if (decisions && decisions.length > 0) {
        return decisions.map((dec) => `${dec.type ?? "?"}${dec.target ? `→${dec.target}` : ""}`).join(", ");
      }
      return null;
    }
    case "situation": {
      const mc = data.message_count as number | undefined;
      const taskCount = data.task_count as number | undefined;
      if (mc == null) return null;
      return `${t("nMessages", { count: mc })} / ${t("nTasks", { count: taskCount ?? 0 })}`;
    }
    case "context_build": {
      const mm = data.memory_msgs_count as number | undefined;
      const tools = data.tool_count as number | undefined;
      const parts: string[] = [];
      if (mm != null) parts.push(`${t("memory")}:${mm}`);
      if (tools != null) parts.push(`${t("toolCount")}:${tools}`);
      return parts.length > 0 ? parts.join(" ") : null;
    }
    case "introspection": {
      const entity = data.entity as string | undefined;
      return entity || null;
    }
    default:
      return null;
  }
}

function TraceNodeComponent({ data, selected }: NodeProps) {
  const { t } = useTranslation("thinking");
  const d = data as unknown as TraceNodeData;
  const fallback = TYPE_STYLES.phase_change!;
  const style = TYPE_STYLES[d.nodeType] ?? fallback;
  const Icon = style.icon;
  const StatusIcon = STATUS_ICON[d.status];
  const subtitle = getSubtitle(d, t);

  return (
    <>
      <Handle type="target" position={Position.Top} className="!bg-[var(--border-strong)] !w-2 !h-2" />
      <div
        className={cn(
          "px-3 py-2 rounded-[var(--radius-md)] border min-w-[180px] max-w-[300px]",
          "shadow-[var(--shadow-sm)] transition-all duration-150",
          d.status === "error"
            ? "bg-red-500/15 border-red-500 border-2"
            : cn(style.bg, style.border),
          selected && "ring-2 ring-[var(--accent)] ring-offset-1 ring-offset-[var(--bg)]",
          d.status === "running" && "animate-[pulse-subtle_2s_ease-in-out_infinite]",
        )}
      >
        <div className="flex items-center gap-2">
          <Icon size={14} className={cn(style.accent, "shrink-0")} />
          <span className="text-xs font-medium text-[var(--text-strong)] truncate flex-1">
            {d.label}
          </span>
          {StatusIcon && (
            <StatusIcon
              size={12}
              className={cn(
                "shrink-0",
                d.status === "running" && "text-[var(--accent)] animate-spin",
                d.status === "completed" && "text-[var(--ok)]",
                d.status === "error" && "text-[var(--danger)]",
              )}
            />
          )}
        </div>
        {subtitle && (
          <div className="mt-0.5 text-[10px] text-[var(--muted)] truncate max-w-[260px]">
            {subtitle}
          </div>
        )}
        {d.duration_ms != null && (
          <div className="mt-0.5 text-[10px] text-[var(--muted)] font-mono">
            {d.duration_ms >= 1000
              ? `${(d.duration_ms / 1000).toFixed(1)}s`
              : `${Math.round(d.duration_ms)}ms`}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Bottom} className="!bg-[var(--border-strong)] !w-2 !h-2" />
    </>
  );
}

export default memo(TraceNodeComponent);
