/**
 * PlanCard — 消息流中的计划卡片。
 *
 * 渲染 PlanRecord 的完整结构化视图：goal / steps（带勾选）/ files / risks / 状态徽标。
 * 与 PlanPanel 浮窗共享 plan-store，状态实时同步。
 */
import { useTranslation } from "react-i18next";
import { CheckCircle2, FileText, Loader2, Target, XCircle, AlertTriangle, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import type { PlanRecord } from "@/lib/types";
import { PlanStepRow } from "@/components/plan/PlanStepRow";

interface Props {
  plan: PlanRecord;
}

export function PlanCard({ plan }: Props) {
  const { t } = useTranslation("plan");
  const [expanded, setExpanded] = useState(true);

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
            {plan.goal || t("card.untitled", { defaultValue: "执行计划" })}
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
                <PlanStepRow key={step.index} step={step} compact />
              ))}
            </div>

            {/* 文件 / 风险 */}
            {(plan.files || plan.risks) && (
              <div className="pt-2 border-t border-border/50 space-y-1">
                {plan.files && (
                  <div className="flex items-start gap-2 text-xs">
                    <FileText size={11} className="text-muted shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <span className="text-muted mr-1.5">{t("card.files", { defaultValue: "涉及" })}</span>
                      <span className="text-foreground/80 break-words">{plan.files}</span>
                    </div>
                  </div>
                )}
                {plan.risks && (
                  <div className="flex items-start gap-2 text-xs">
                    <AlertTriangle size={11} className="text-yellow-500 shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <span className="text-muted mr-1.5">{t("card.risks", { defaultValue: "风险" })}</span>
                      <span className="text-yellow-600 dark:text-yellow-400 break-words">{plan.risks}</span>
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* 状态徽标 */}
            <div className="flex items-center gap-2 pt-1.5 border-t border-border/50 text-[11px]">
              <PlanStatusBadge plan={plan} />
              {inProgress && (
                <span className="text-muted">
                  {t("card.workingOn", { defaultValue: "进行中" })}: {inProgress.content}
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

function PlanStatusBadge({ plan }: { plan: PlanRecord }) {
  const { t } = useTranslation("plan");
  if (plan.status === "cancelled") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-300">
        <XCircle size={10} />
        {t("status.cancelled", { defaultValue: "已取消" })}
      </span>
    );
  }
  if (plan.status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-300">
        <CheckCircle2 size={10} />
        {t("status.completed", { defaultValue: "已完成" })}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-accent-subtle text-accent">
      <Loader2 size={10} className="animate-spin" />
      {t("status.executing", { defaultValue: "执行中" })}
    </span>
  );
}
