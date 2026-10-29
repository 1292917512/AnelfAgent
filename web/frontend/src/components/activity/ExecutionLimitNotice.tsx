import { CircleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityLimit } from "@/lib/types";
import { ConfigLink } from "@/components/common/ConfigLink";

/** 展示执行层报告的真实限制来源，配置入口复用公共导航机制。 */
export function ExecutionLimitNotice({ limit }: { limit: ActivityLimit }) {
  const { t } = useTranslation(["workbench", "config"]);
  return <div role="alert" className="my-2 space-y-2 rounded-md border border-warn/30 bg-warn/5 p-3 text-sm">
    <p className="flex items-start gap-2 text-warn"><CircleAlert size={16} className="mt-0.5 shrink-0" aria-hidden="true" />{t("activity.iterationLimit", { count: limit.value })}</p>
    <p className="text-xs text-muted">{t(limit.config_keys.length ? "activity.iterationLimitHint" : "activity.iterationBudgetHint")}</p>
    {limit.config_keys.length > 0 && <div className="flex flex-wrap gap-x-4 gap-y-2">{limit.config_keys.map((key) => <ConfigLink key={key} config={{ key }}>
      {t("config:navigation.open")}<code className="min-w-0 break-all">{key}</code>
    </ConfigLink>)}</div>}
  </div>;
}
