/** 计划涉及的文件与风险说明。 */
import { useTranslation } from "react-i18next";
import { AlertTriangle, FileText } from "lucide-react";
import type { PlanRecord } from "@/lib/types";

interface Props {
  plan: Pick<PlanRecord, "files" | "risks">;
}

export function PlanMeta({ plan }: Props) {
  const { t } = useTranslation("plan");
  if (!plan.files && !plan.risks) return null;
  return (
    <>
      {plan.files && (
        <div className="flex items-start gap-2 text-xs pt-1.5">
          <FileText size={11} className="text-muted shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <span className="text-muted mr-1">{t("card.files")}</span>
            <span className="text-foreground/80 break-words text-[11px]">{plan.files}</span>
          </div>
        </div>
      )}
      {plan.risks && (
        <div className="flex items-start gap-2 text-xs">
          <AlertTriangle size={11} className="text-yellow-500 shrink-0 mt-0.5" />
          <div className="flex-1 min-w-0">
            <span className="text-muted mr-1">{t("card.risks")}</span>
            <span className="text-yellow-600 dark:text-yellow-400 break-words text-[11px]">
              {plan.risks}
            </span>
          </div>
        </div>
      )}
    </>
  );
}
