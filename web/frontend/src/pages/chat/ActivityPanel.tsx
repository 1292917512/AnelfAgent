/**
 * ActivityPanel — 执行活动弹层（锚定 ActivityBar 上方）。
 *
 * 聚合当前会话的「计划 + 子代理」实时视图：
 * - 计划节：goal / 进度 / 步骤（复用 PlanStepRow + PlanStatusBadge）
 * - 子代理节：复用 DelegationCard（实时进度 / 取消 / 展开详情），running 置顶
 * 点击遮罩收起。
 */
import { useTranslation } from "react-i18next";
import { Target } from "lucide-react";
import { useChatStore } from "@/stores/chat-store";
import { usePlanStore } from "@/stores/plan-store";
import { useDelegationStore } from "@/stores/delegation-store";
import { PlanStepRow } from "@/components/plan/PlanStepRow";
import { PlanStatusBadge } from "@/components/plan/PlanStatusBadge";
import { DelegationCard } from "./render/DelegationCard";

interface Props {
  onClose: () => void;
}

export function ActivityPanel({ onClose }: Props) {
  const { t } = useTranslation("plan");
  const activeChatId = useChatStore((s) => s.activeChatId);
  const activePlan = usePlanStore((s) => s.getActivePlan(activeChatId));
  const chatDelegations = useDelegationStore((s) => s.delegations[activeChatId]);

  // running 置顶，其余按开始时间倒序
  const delegations = [...Object.values(chatDelegations ?? {})].sort(
    (a, b) =>
      Number(b.status === "running") - Number(a.status === "running") ||
      b.started_at - a.started_at,
  );

  const done = activePlan
    ? activePlan.steps.filter((s) => s.status === "completed").length
    : 0;

  return (
    <>
      {/* 点击外部收起 */}
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className="absolute inset-x-0 bottom-full mb-2 z-30 rounded-lg border border-border bg-card shadow-xl max-h-[60vh] overflow-y-auto">
        <div className="px-3 py-2 border-b border-border/50 text-xs font-medium text-muted">
          {t("activity.title")}
        </div>

        {activePlan && (
          <div className="px-3 py-2 border-b border-border/50 space-y-1">
            <div className="text-[10px] text-muted">{t("activity.plan")}</div>
            <div className="flex items-center gap-2 text-xs">
              <Target size={12} className="text-accent shrink-0" />
              <span className="flex-1 min-w-0 truncate font-medium text-foreground">
                {activePlan.goal || t("panel.untitled")}
              </span>
              <span className="text-muted font-mono shrink-0">
                {done}/{activePlan.steps.length}
              </span>
              <PlanStatusBadge plan={activePlan} />
            </div>
            <div className="space-y-0.5">
              {activePlan.steps.map((step) => (
                <PlanStepRow
                  key={step.index}
                  step={step}
                  compact
                  settled={activePlan.status !== "executing"}
                />
              ))}
            </div>
          </div>
        )}

        {delegations.length > 0 ? (
          <div className="p-2 space-y-2">
            <div className="px-1 text-[10px] text-muted">{t("activity.delegations")}</div>
            {delegations.map((d) => (
              <DelegationCard key={d.delegation_id} node={d} />
            ))}
          </div>
        ) : (
          !activePlan && (
            <div className="text-xs text-muted text-center py-3">
              {t("activity.empty")}
            </div>
          )
        )}
      </div>
    </>
  );
}
