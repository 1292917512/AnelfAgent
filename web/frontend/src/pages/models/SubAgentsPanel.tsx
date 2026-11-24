/**
 * SubAgentsPanel — 子代理统一注册表管理。
 *
 * 一套档案体系：内置难度档（easy/medium/hard，delegate_task 的 difficulty
 * 1/2/3 即其语法糖，池内顺序即优先级、降挡回退）与自定义档案（agent_name
 * 直指，单模型或降级链）同构存储、同套 CRUD。增删改即时持久化并热生效。
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { subAgentsApi } from "@/lib/api";
import type { SubAgentProfile } from "@/lib/types";
import { usePriorities } from "@/components/models/ModelSelect";
import { BuiltinTierSection } from "@/pages/models/subagents/BuiltinTierSection";
import { CustomAgentSection } from "@/pages/models/subagents/CustomAgentSection";

export function SubAgentsPanel() {
  const { t } = useTranslation("models");
  const qc = useQueryClient();

  const { data: profiles = [], isPending, error, refetch } = useQuery<SubAgentProfile[]>({
    queryKey: ["subAgents"], throwOnError: false,
    queryFn: () => subAgentsApi.list().then((r) => r.data.sub_agents),
  });
  const { data: priorities = {} } = usePriorities();
  const chatItems = priorities.chat ?? [];

  const invalidate = useCallback(() => {
    return qc.invalidateQueries({ queryKey: ["subAgents"] });
  }, [qc]);

  const builtins = profiles.filter((p) => p.builtin);
  const customs = profiles.filter((p) => !p.builtin);

  if (isPending) return <PageSkeleton />;
  if (error && !profiles.length) return <QueryError error={error} retry={() => void refetch()} />;

  return (
    <div className="space-y-5">
      {error && <QueryError compact error={error} retry={() => void refetch()} />}
      <div className="space-y-1">
        <p className="text-sm text-muted">{t("subagents.desc")}</p>
        <p className="text-xs text-muted">{t("subagents.usageHint")}</p>
      </div>

      <div className="space-y-3">
        <div className="space-y-1">
          <p className="text-sm font-medium text-heading">{t("subagents.builtinTitle")}</p>
          <p className="text-xs text-muted">{t("subagents.builtinHint")}</p>
        </div>
        {builtins.map((profile) => (
          <BuiltinTierSection
            key={profile.name}
            profile={profile}
            chatItems={chatItems}
            onChanged={invalidate}
          />
        ))}
      </div>

      <CustomAgentSection profiles={customs} onChanged={invalidate} />
    </div>
  );
}
