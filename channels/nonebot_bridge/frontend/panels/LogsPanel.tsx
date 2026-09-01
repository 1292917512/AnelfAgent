import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { nonebotApi } from "../api";
import { EmptyState, LoadingBlock } from "@/components/ui";

/** worker 日志尾部视图（轮询刷新） */
export function LogsPanel() {
  const { t } = useTranslation("nonebot");

  const { data, isLoading } = useQuery({
    queryKey: ["nonebotLogs"],
    queryFn: () => nonebotApi.logs(300).then((r) => r.data),
    refetchInterval: 3000,
  });

  if (isLoading) return <LoadingBlock label={t("common:loading")} />;

  const logs = data?.logs || [];
  if (logs.length === 0) {
    return (
      <EmptyState
        icon={ScrollText}
        title={t("logs.empty")}
        description={t("logs.emptyHint")}
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{t("logs.tailHint", { count: logs.length })}</span>
        <span>{t("logs.refreshHint")}</span>
      </div>
      <pre className="max-h-[70vh] overflow-auto rounded-lg border border-border bg-elevated p-3 text-xs leading-relaxed whitespace-pre-wrap break-all">
        {logs.join("\n")}
      </pre>
    </div>
  );
}
