/**
 * PlanCard — 消息流中的计划卡片。
 *
 * 渲染 PlanRecord 的完整结构化视图：goal / steps（带勾选）/ files / risks / 状态徽标。
 * 与 PlanPanel 浮窗共享 plan-store 与展示子组件（PlanStepRow / PlanStatusBadge / PlanMeta）。
 */
import { useTranslation } from "react-i18next";
import { Target, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { PlanRecord } from "@/lib/types";
import { PlanStepRow } from "@/components/plan/PlanStepRow";
import { PlanStatusBadge } from "@/components/plan/PlanStatusBadge";
import { PlanMeta } from "@/components/plan/PlanMeta";

interface Props {
  plan: PlanRecord;
}

export function PlanCard({ plan }: Props) {
  const { t } = useTranslation("plan");
  // 默认折叠：卡片是计划的过程记录，展开细节按需查看，避免长步骤列表刷屏
  const [expanded, setExpanded] = useState(false);

  const total = plan.steps.length;
  const done = plan.steps.filter((s) => s.status === "completed").length;
  const inProgress = plan.steps.find((s) => s.status === "in_progress");
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          "max-w-[92%] sm:max-w-[88%] w-full rounded-lg border overflow-hidden",
          plan.status === "cancelled"
            ? "border-red-400/40 bg-red-50/40 dark:bg-red-950/20"
            : plan.status === "completed"
              ? "border-green-400/40 bg-green-50/30 dark:bg-green-950/20"
              : "border-accent/40 bg-accent-subtle/30",
        )}
      >
        {/* 头部 */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-hover/50 transition-colors"
        >
          <Target size={14} className={cn("shrink-0", plan.status === "cancelled" ? "text-red-500" : "text-accent")} />
          <span className="text-sm font-medium text-foreground flex-1 min-w-0 truncate">
            {plan.goal || t("card.untitled")}
          </span>
          <span className="text-xs text-muted shrink-0 font-mono">
            {done}/{total}
          </span>
          {expanded ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
        </button>

        {/* 进度条 */}
        {total > 0 && (
          <div className="px-3 pb-1">
            <div className="h-1 bg-border rounded-full overflow-hidden">
              <div
                className={cn(
                  "h-full transition-all duration-300",
                  plan.status === "cancelled" ? "bg-red-400" : "bg-accent",
                )}
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )}

        {expanded && (
          <div className="px-3 pb-2 space-y-2">
            {/* 步骤 */}
            <div className="space-y-0.5 mt-1">
              {plan.steps.map((step) => (
                <PlanStepRow
                  key={step.index}
                  step={step}
                  compact
                  settled={plan.status !== "executing"}
                />
              ))}
            </div>

            {/* 文件 / 风险 */}
            {(plan.files || plan.risks) && (
              <div className="pt-2 border-t border-border/50 space-y-1">
                <PlanMeta plan={plan} />
              </div>
            )}

            {/* 状态徽标 */}
            <div className="flex items-center gap-2 pt-1.5 border-t border-border/50 text-[11px]">
              <PlanStatusBadge plan={plan} />
              {inProgress && (
                <span className="text-muted">
                  {t("card.workingOn")}: {inProgress.content}
                </span>
              )}
              {plan.cancel_reason && (
                <span className="text-red-500 truncate">
                  {plan.cancel_reason}
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
