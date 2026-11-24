import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Wrench } from "lucide-react";
import { runtimeThinkingQuery } from "@/lib/runtime-queries";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";

export function ToolsInsightPanel() {
  const { t } = useTranslation("status");
  const query = useQuery(runtimeThinkingQuery());
  const snapshot = query.data;
  if (query.error || !snapshot?.tool_recall) return <Card title={t("toolCallRanking")}>
    {query.error ? <QueryError compact error={query.error} retry={() => void query.refetch()} /> :
      <p className="text-sm text-muted">{t(query.isPending ? "common:loading" : "common:unavailable")}</p>}
  </Card>;
  const toolRecall = [...snapshot.tool_recall].sort((a, b) => b.count - a.count);
  const maxCount = Math.max(1, ...toolRecall.map((tool) => tool.count));
  return (
    <Card title={t("toolCallRanking")} subtitle={t("dashboard:overview.toolRankingHint")} actions={<Wrench size={16} className="text-muted" />}>
      <div className="overview-tool-summary">
        <span>{t("recalledTools")} <strong>{toolRecall.length}</strong></span>
        <span>{t("discoveredTools")} <strong>{snapshot.discovered_tools?.length ?? "—"}</strong></span>
        <span>{t("tagActivated")} <strong>{snapshot.tag_activated_tools?.length ?? "—"}</strong></span>
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
        {!toolRecall.length && <p className="py-6 text-sm text-muted">{t("noToolCalls")}</p>}
      </div>
      <details className="overview-tool-details">
        <summary>{t("discoveredTools")} / {t("tagActivatedTools")}</summary>
        {[{ title: t("discoveredTools"), names: snapshot.discovered_tools, empty: t("noDiscoveredTools") }, { title: t("tagActivatedTools"), names: snapshot.tag_activated_tools, empty: t("noTagTools") }].map((group) => <div key={group.title}>
          <h3>{group.title}</h3>
          <div className="flex flex-wrap gap-1.5">{group.names?.length ? group.names.map((name) => <span key={name} className="overview-tool-tag">{name}</span>) : <p className="text-xs text-muted">{group.names ? group.empty : t("common:unavailable")}</p>}</div>
        </div>)}
      </details>
    </Card>
  );
}
