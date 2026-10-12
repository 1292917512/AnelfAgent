import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Brain, MessageSquare, Webhook, Workflow } from "lucide-react";
import { hooksApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { HookExecutionRow } from "@/components/common/HookExecutionRow";
import { Button, EmptyState, LoadingBlock } from "@/components/ui";
import { ActivityDetailsProvider } from "../chat/activity/ActivityDetailsProvider";

export function HookOverview() {
  const { t } = useTranslation("settings");
  const query = useQuery({
    queryKey: ["hooks-config"],
    queryFn: () => hooksApi.get().then((r) => r.data),
    refetchInterval: 3000,
  });
  const stages = [
    { key: "prepare", icon: Brain },
    { key: "tools", icon: Webhook },
    { key: "complete", icon: MessageSquare },
  ];
  const records = [
    ...(query.data?.execution?.active ?? []),
    ...(query.data?.execution?.recent ?? []),
  ];
  return (
    <ActivityDetailsProvider><div className="space-y-5">
      <div className="grid gap-3 lg:grid-cols-3">
        {stages.map(({ key, icon: Icon }, index) => (
          <Card key={key} className="p-5">
            <div className="mb-4 flex items-center gap-3">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent/10 text-accent">
                <Icon size={18} />
              </span>
              <span className="ml-auto text-xs tabular-nums text-muted">
                0{index + 1}
              </span>
            </div>
            <h3 className="text-sm font-semibold text-heading">
              {t(`hookFlow.stages.${key}.title`)}
            </h3>
            <p className="mt-2 text-xs leading-6 text-muted">
              {t(`hookFlow.stages.${key}.body`)}
            </p>
          </Card>
        ))}
      </div>
      <Card title={t("hookFlow.recent")} subtitle={t("hookFlow.retention")}>
        {query.isError ? (
          <Button variant="secondary" onClick={() => query.refetch()}>
            {t("common:retry")}
          </Button>
        ) : query.isPending ? (
          <LoadingBlock />
        ) : records.length ? (
          <div className="space-y-2">
            {records.slice(0, 30).map((run) => (
              <HookExecutionRow key={run.id} run={run} />
            ))}
          </div>
        ) : (
          <EmptyState icon={Workflow} title={t("hookFlow.empty")} />
        )}
      </Card>
    </div></ActivityDetailsProvider>
  );
}
