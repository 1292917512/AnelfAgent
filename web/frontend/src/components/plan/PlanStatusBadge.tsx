/**
 * PlanStatusBadge — 计划状态徽标（executing / completed / cancelled）。
 *
 * 被 PlanPanel（浮窗）与 PlanCard（消息卡）共享，消除逐字重复。
 */
import { useTranslation } from "react-i18next";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { PlanRecord } from "@/lib/types";

export function PlanStatusBadge({ plan }: { plan: PlanRecord }) {
  const { t } = useTranslation("plan");
  if (plan.status === "cancelled") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-300 text-[11px]">
        <XCircle size={10} />
        {t("status.cancelled", { defaultValue: "已取消" })}
      </span>
    );
  }
  if (plan.status === "completed") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-300 text-[11px]">
        <CheckCircle2 size={10} />
        {t("status.completed", { defaultValue: "已完成" })}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-accent-subtle text-accent text-[11px]">
      <Loader2 size={10} className="animate-spin" />
      {t("status.executing", { defaultValue: "执行中" })}
    </span>
  );
}
