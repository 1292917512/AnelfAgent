/**
 * PlanStepRow — 单步骤行（用于 PlanCard / PlanPanel / ActivityPanel）。
 *
 * 状态图标 + 内容 + 备注 + 进行中标识。
 * settled 表示 plan 已进入终态：残留的 in_progress 步骤按 skipped 渲染，
 * 不再转圈（后端收敛为主，此为展示层兜底）。
 */
import { CheckCircle2, Circle, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PlanStep } from "@/lib/types";

interface Props {
  step: PlanStep;
  compact?: boolean;
  showIndex?: boolean;
  settled?: boolean;
}

const STATUS_ICON: Record<PlanStep["status"], typeof Circle> = {
  pending: Circle,
  in_progress: Loader2,
  completed: CheckCircle2,
  skipped: XCircle,
};

const STATUS_COLOR: Record<PlanStep["status"], string> = {
  pending: "text-muted",
  in_progress: "text-accent animate-spin",
  completed: "text-green-500",
  skipped: "text-muted line-through opacity-60",
};

export function PlanStepRow({ step, compact = false, showIndex = true, settled = false }: Props) {
  const status = settled && step.status === "in_progress" ? "skipped" : step.status;
  const Icon = STATUS_ICON[status] ?? Circle;
  const color = STATUS_COLOR[status] ?? "text-muted";
  return (
    <div
      className={cn(
        "flex items-start gap-2 text-xs",
        compact ? "py-0.5" : "py-1",
        status === "completed" && "opacity-70",
        status === "skipped" && "opacity-50",
      )}
    >
      <Icon size={compact ? 11 : 13} className={cn("shrink-0 mt-0.5", color)} />
      {showIndex && (
        <span className="text-muted font-mono shrink-0 min-w-[14px]">{step.index + 1}.</span>
      )}
      <span
        className={cn(
          "flex-1 min-w-0 break-words",
          status === "skipped" && "line-through text-muted",
          status === "completed" && "text-muted",
          status === "in_progress" && "text-foreground font-medium",
          status === "pending" && "text-foreground/80",
        )}
      >
        {step.content}
      </span>
      {step.note && (
        <span className="text-[10px] text-muted shrink-0 max-w-[40%] truncate" title={step.note}>
          {step.note}
        </span>
      )}
    </div>
  );
}
