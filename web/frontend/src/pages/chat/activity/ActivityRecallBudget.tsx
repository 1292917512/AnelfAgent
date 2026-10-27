import { useTranslation } from "react-i18next";
import type { RecallBudgetReport } from "@/lib/types/activity";

/** 准备结果与最终组装分开计量；提示文字不计为检索证据。 */
export function ActivityRecallBudget({ report }: { report: RecallBudgetReport }) {
  const { t } = useTranslation("workbench");
  const groups = [["profiles", report.profiles], ["evidence", report.evidence]] as const;
  const fields = ["prepared_blocks", "injected_blocks", "truncated_blocks", "omitted_blocks"] as const;
  return <section aria-label={t("activity.recallBudget.title")} className="space-y-2 rounded-md border border-border p-3">
    <p className="text-xs font-medium text-heading">{t(`activity.recallBudget.levels.${report.level}`, { defaultValue: report.level })}
      <span className="ml-2 font-normal text-muted">{t("activity.recallBudget.limit", { count: report.budget_chars })}</span>
    </p>
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {groups.map(([name, group]) => <section key={name} aria-label={t(`activity.recallBudget.${name}`)}>
        <h4 className="text-xs font-medium text-heading">{t(`activity.recallBudget.${name}`)}</h4>
        <dl className="mt-1 grid grid-cols-2 gap-1 text-xs">
          {fields.map(field => <div key={field} title={t(`activity.recallBudget.${field}Hint`)}>
            <dt className="text-muted">{t(`activity.recallBudget.${field}`)}</dt>
            <dd className="tabular-nums text-foreground">{group[field]}</dd>
          </div>)}
        </dl>
        <p className="mt-1 text-xs text-muted">{t("activity.recallBudget.characters", { prepared: group.prepared_chars, injected: group.injected_chars })}</p>
      </section>)}
    </div>
    <p className="text-xs text-muted">{t("activity.recallBudget.hint")}</p>
  </section>;
}
