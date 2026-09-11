import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Search, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui";
import { Button } from "@/components/ui";
import { Card } from "@/components/common/Card";
import { EmptyState } from "@/components/ui";
import { Input } from "@/components/ui";
import { Select } from "@/components/ui";
import { Spinner } from "@/components/ui";
import { memoryApi } from "@/lib/api";
import type { RecallTestItem, RecallTestResult } from "@/lib/types";

const SOURCE_ORDER: RecallTestItem["source"][] = ["memory", "file", "cognee_chunk", "cognee_graph"];

const SOURCE_BADGE: Record<RecallTestItem["source"], { variant: "neutral" | "info" | "ok" | "warn" | "danger"; key: string }> = {
  memory: { variant: "info", key: "recall.sourceMemory" },
  file: { variant: "neutral", key: "recall.sourceFile" },
  cognee_chunk: { variant: "ok", key: "recall.sourceCogneeChunk" },
  cognee_graph: { variant: "warn", key: "recall.sourceCogneeGraph" },
};

function SourceGroup({ source, items }: { source: RecallTestItem["source"]; items: RecallTestItem[] }) {
  const { t } = useTranslation("memory");
  const badge = SOURCE_BADGE[source];
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Badge variant={badge.variant}>{t(badge.key)}</Badge>
        <span className="text-xs text-muted">{items.length}</span>
      </div>
      {items.map((item) => (
        <div key={item.id} className="rounded-md border border-border bg-elevated p-2.5 text-sm">
          <div className="flex items-start justify-between gap-2">
            <p className="flex-1 break-words whitespace-pre-wrap text-heading">{item.content}</p>
            <span className="flex-shrink-0 rounded bg-surface px-1.5 py-0.5 font-mono text-xs text-muted">
              {item.score.toFixed(3)}
            </span>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted">
            {item.dataset && <span className="font-mono">{item.dataset}</span>}
            {item.path && <span className="truncate max-w-64">{item.path}</span>}
            {item.tags.slice(0, 4).map((tag) => (
              <span key={tag} className="rounded bg-surface px-1.5 py-0.5">{tag}</span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * 召回测试面板：输入查询文本，按真实召回管线（检索规划 → 多查询共识融合 →
 * 关系/遗忘层）无副作用执行，展示规划决策、各来源结果与耗时分解。
 * preset="cognee" 时默认勾选 cognee 检索类型（嵌入 Cognee 面板用）。
 */
export function RecallTester({ preset = "all" }: { preset?: "all" | "cognee" }) {
  const { t } = useTranslation("memory");
  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState<"shallow" | "deep">("shallow");
  const [tags, setTags] = useState("");
  const [useCogneeTypes, setUseCogneeTypes] = useState(preset === "cognee");
  const [result, setResult] = useState<RecallTestResult | null>(null);

  const runMutation = useMutation({
    mutationFn: async () => {
      const tagList = tags.split(",").map((s) => s.trim()).filter(Boolean);
      const { data } = await memoryApi.recallTest({
        query,
        depth,
        tags: tagList.length ? tagList : undefined,
        limit: 8,
        ...(useCogneeTypes ? { search_types: ["CHUNKS", "CHUNKS_LEXICAL", "GRAPH_COMPLETION"] } : {}),
      });
      return data;
    },
    onSuccess: (data) => setResult(data),
    onError: () => setResult(null),
  });

  const grouped = SOURCE_ORDER
    .map((source) => ({
      source,
      items: (result?.items ?? []).filter((item) => item.source === source),
    }))
    .filter((group) => group.items.length > 0);

  return (
    <Card className="p-4 space-y-4">
      <div className="space-y-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("recall.placeholder")}
          onKeyDown={(e) => {
            if (e.key === "Enter" && query.trim() && !runMutation.isPending) runMutation.mutate();
          }}
        />
        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={depth}
            onChange={(e) => setDepth(e.target.value as "shallow" | "deep")}
            className="w-28"
          >
            <option value="shallow">{t("recall.shallow")}</option>
            <option value="deep">{t("recall.deep")}</option>
          </Select>
          <Input
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder={t("recall.tagsPlaceholder")}
            className="w-56"
          />
          <label className="flex items-center gap-1.5 text-xs text-muted">
            <input
              type="checkbox"
              checked={useCogneeTypes}
              onChange={(e) => setUseCogneeTypes(e.target.checked)}
            />
            {t("recall.cogneeTypes")}
          </label>
          <Button
            variant="primary"
            size="sm"
            disabled={!query.trim() || runMutation.isPending}
            onClick={() => runMutation.mutate()}
          >
            {runMutation.isPending ? <Spinner className="h-4 w-4" /> : <Search className="h-4 w-4" />}
            {t("recall.run")}
          </Button>
        </div>
      </div>

      {runMutation.isError && (
        <p className="text-sm text-danger">{t("recall.failed")}</p>
      )}

      {result && !runMutation.isError && (
        <div className="space-y-4">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <div className="rounded-md border border-border bg-elevated p-3 space-y-1.5">
              <p className="text-xs font-medium text-heading">{t("recall.planTitle")}</p>
              {result.plan.queries.map((q) => (
                <p key={q} className="flex items-start gap-1 text-xs text-muted">
                  <ChevronRight className="mt-0.5 h-3 w-3 flex-shrink-0" />
                  <span className="break-words">{q}</span>
                </p>
              ))}
              {result.plan.entities.length > 0 && (
                <p className="text-xs text-muted">
                  {t("recall.entities")}: {result.plan.entities.join("、")}
                </p>
              )}
              <div className="flex items-center gap-2 text-xs">
                <span className={result.plan.deep_needed ? "text-warning" : "text-muted"}>
                  {t("recall.deepNeeded")}: {result.plan.deep_needed ? t("recall.yes") : t("recall.no")}
                </span>
                {result.plan.rationale && (
                  <span className="truncate text-muted" title={result.plan.rationale}>
                    {result.plan.rationale}
                  </span>
                )}
              </div>
            </div>
            <div className="rounded-md border border-border bg-elevated p-3 space-y-1.5">
              <p className="text-xs font-medium text-heading">{t("recall.pipelineTitle")}</p>
              <div className="flex flex-wrap gap-1.5 text-xs text-muted">
                {result.cognee.datasets.map((d) => (
                  <span key={d} className="rounded bg-surface px-1.5 py-0.5 font-mono">{d}</span>
                ))}
              </div>
              <div className="flex flex-wrap gap-1.5 text-xs text-muted">
                {result.cognee.search_types.map((s) => (
                  <span key={s} className="rounded bg-surface px-1.5 py-0.5 font-mono">{s}</span>
                ))}
              </div>
              <p className="text-xs text-muted">
                {t("recall.timings")}: {t("recall.planMs", { ms: result.timings.plan_ms ?? 0 })}
                {" / "}
                {t("recall.searchMs", { ms: result.timings.search_ms ?? 0 })}
                {" / "}
                {t("recall.totalMs", { ms: result.timings.total_ms ?? 0 })}
              </p>
            </div>
          </div>

          {grouped.length === 0 && result.relations.length === 0 && result.forgotten.length === 0 ? (
            <EmptyState title={t("recall.empty")} />
          ) : (
            <div className="space-y-4">
              {grouped.map((group) => (
                <SourceGroup key={group.source} source={group.source} items={group.items} />
              ))}
              {result.relations.length > 0 && (
                <div className="space-y-1.5">
                  <Badge variant="info">{t("recall.relations")}</Badge>
                  {result.relations.map((rel) => (
                    <p key={rel} className="rounded-md border border-border bg-elevated p-2 font-mono text-xs text-heading break-words">
                      {rel}
                    </p>
                  ))}
                </div>
              )}
              {result.forgotten.length > 0 && (
                <div className="space-y-1.5">
                  <Badge variant="danger">{t("recall.forgotten")}</Badge>
                  {result.forgotten.map((item) => (
                    <p key={String(item.id)} className="rounded-md border border-border border-dashed bg-elevated p-2 text-xs text-muted break-words">
                      [{item.kind ?? "archived"}] {item.content ?? item.gist ?? ""}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
