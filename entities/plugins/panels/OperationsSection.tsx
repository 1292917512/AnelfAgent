import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { AsyncState } from "@/components/common/AsyncState";
import { ChevronRight } from "lucide-react";
import { Badge, Button } from "@/components/ui";
import { pluginsApi } from "./api";

const actions: Record<string, string> = {
  "安装": "install", "升级": "upgrade", "移除": "remove", "启用": "enabled", "禁用": "disabled",
  "刷新市场": "refresh", "订阅市场": "addMarketplace",
};

export function OperationsSection() {
  const { t } = useTranslation("plugins");
  const query = useQuery({
    queryKey: ["plugins-operations"], queryFn: () => pluginsApi.operations().then((r) => r.data),
    refetchInterval: (q) => q.state.data?.some((row) => row.status === "active") ? 2000 : false,
    throwOnError: false,
  });
  return <section className="space-y-3">
    <Button size="sm" loading={query.isFetching} onClick={() => void query.refetch()}>{t("refresh")}</Button>
    <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
      {!query.data?.length && <p className="text-sm text-muted">{t("noOperations")}</p>}
      {query.data?.map((row) => <details key={row.id} className="group rounded-lg border border-border bg-elevated p-3 text-sm">
        <summary className="grid cursor-pointer list-none grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-2">
          <span className="truncate font-medium">{t(actions[row.action] ?? row.action)} · {row.name}</span>
          <Badge variant={row.status === "failed" ? "danger" : row.status === "active" ? "info" : "ok"}>{t(row.status)}</Badge>
          <ChevronRight size={14} className="text-muted group-open:rotate-90" />
          <span className="col-span-3 mt-1 text-xs text-muted">{new Date(row.started_at * 1000).toLocaleString()}
            {row.status !== "succeeded" && " · " + t("stage_" + row.stage)}</span>
          {row.error && <span className="col-span-3 mt-1 break-all text-danger">{row.error}</span>}
        </summary>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-t border-border pt-2 text-xs">
          <dt>{t("target")}</dt><dd className="break-all">{row.name}</dd>
          <dt>{t("operationId")}</dt><dd className="break-all font-mono">{row.id}</dd>
        </dl>
      </details>)}
    </AsyncState>
  </section>;
}
