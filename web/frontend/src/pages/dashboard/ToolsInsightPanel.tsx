import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { statusApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";

export function ToolsInsightPanel() {
  const { t } = useTranslation("status");
  const query = useQuery({ queryKey: ["pfc"], queryFn: () => statusApi.pfc().then((r) => r.data), refetchInterval: 3000 });
  const pfc = query.data;
  const toolRecall = [...(pfc?.tool_recall ?? [])].sort((a, b) => b.count - a.count);
  const maxCount = Math.max(1, ...toolRecall.map((tool) => tool.count));
  return (
    <Card title={t("toolCallRanking")} subtitle={t("dashboard:overview.toolRankingHint")} actions={<Wrench size={16} className="text-muted" />}>
      {query.error && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
      <div className="overview-tool-summary">
        <span>{t("recalledTools")} <strong>{toolRecall.length}</strong></span>
        <span>{t("activeTools")} <strong>{pfc?.active_tools?.length ?? 0}</strong></span>
        <span>{t("tagActivated")} <strong>{pfc?.tag_activated_tools?.length ?? 0}</strong></span>
      </div>
      <div className="overview-ranking">
        {toolRecall.map((tool, index) => <div key={tool.name} className="overview-ranking-row">
          <span className="overview-ranking-index">{String(index + 1).padStart(2, "0")}</span>
          <div className="overview-ranking-track">
            <span className="overview-ranking-fill" style={{ width: `${tool.count / maxCount * 100}%` }} />
            <span className="relative truncate" title={tool.name}>{tool.name}</span>
          </div>
          <strong>{tool.count.toLocaleString()}</strong>
        </div>)}
        {!toolRecall.length && <p className="py-6 text-sm text-muted">{query.isPending ? t("common:loading") : t("noToolCalls")}</p>}
      </div>
      <details className="overview-tool-details">
        <summary>{t("currentActiveTools")} / {t("tagActivatedTools")}</summary>
        {[{ title: t("currentActiveTools"), names: pfc?.active_tools, empty: t("noActiveTools") }, { title: t("tagActivatedTools"), names: pfc?.tag_activated_tools, empty: t("noTagTools") }].map((group) => <div key={group.title}>
          <h3>{group.title}</h3>
          <div className="flex flex-wrap gap-1.5">{group.names?.length ? group.names.map((name) => <span key={name} className="overview-tool-tag">{name}</span>) : <p className="text-xs text-muted">{group.empty}</p>}</div>
        </div>)}
      </details>
    </Card>
  );
}
