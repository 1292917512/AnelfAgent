import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { skillsApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/Modal";
import { QueryError } from "@/components/common/AsyncState";
import { Activity, Boxes, RefreshCw } from "lucide-react";

/** 向量构建卡片：状态机可视化 + 手动重建入口（模型切换后的标准操作）。 */
export function VectorBuildCard() {
  const { t } = useTranslation(["skills", "common"]);
  const queryClient = useQueryClient();
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const { data: health, error, refetch } = useQuery({
    queryKey: ["skills", "health"],
    queryFn: () => skillsApi.health().then((r) => r.data),
    staleTime: 5_000, throwOnError: false,
    refetchInterval: (query) => {
      const state = query.state.data?.build?.state;
      return state === "rebuilding" || state === "warming" ? 2_000 : 30_000;
    },
  });
  const rebuildMutation = useMutation({
    mutationFn: () => skillsApi.rebuildVectors(),
    onSuccess: () => { setConfirmRebuild(false); void queryClient.invalidateQueries({ queryKey: ["skills"] }); },
  });

  if (error) return <QueryError compact error={error} retry={() => void refetch()} />;
  if (!health?.build && !health?.embedding) return null;
  const build = health.build;
  const emb = health.embedding;
  const state = build?.state || (emb?.rebuilding ? "rebuilding" : "idle");
  const progress = build?.progress || { done: emb?.embedded || 0, total: emb?.total || 0 };
  const model = build?.model || emb?.model || "";
  const lastRebuild = build?.last_rebuild;
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className="px-4 py-3 rounded-md border border-border bg-card space-y-2">
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <Boxes size={14} className={state === "rebuilding" ? "text-warn" : "text-ok"} />
        <span className="font-medium text-foreground">{t("vectorBuildTitle")}</span>
        <span className="text-muted">{t("vectorBuildModel")}: {model || t("embeddingNoModel")}</span>
        <span className={cn("text-muted", state === "rebuilding" && "text-warn")}>
          {state === "rebuilding"
            ? t("vectorBuildRebuilding", { done: progress.done, total: progress.total })
            : state === "warming"
              ? t("vectorBuildWarming")
              : t("vectorBuildIdle", { embedded: emb?.embedded ?? progress.done, total: emb?.total ?? progress.total })}
        </span>
        {lastRebuild && (
          <span className="text-muted">
            {t("vectorBuildLastRebuild", {
              count: lastRebuild.count,
              model: lastRebuild.model || "?",
              time: new Date(lastRebuild.at * 1000).toLocaleString(),
            })}
          </span>
        )}
        <div className="ml-auto">
          <Button
            variant="secondary" size="sm"
            onClick={() => setConfirmRebuild(true)}
            disabled={!health.build || state === "rebuilding" || rebuildMutation.isPending}
            loading={rebuildMutation.isPending}
          >
            <RefreshCw size={13} /> {t("vectorBuildRebuild")}
          </Button>
        </div>
      </div>
      <ConfirmDialog open={confirmRebuild} onClose={() => setConfirmRebuild(false)}
        title={t("vectorBuildRebuild")} message={t("vectorBuildRebuildConfirm")}
        loading={rebuildMutation.isPending} onConfirm={() => rebuildMutation.mutate()} />
      {rebuildMutation.error && <QueryError compact error={rebuildMutation.error} />}
      {state === "rebuilding" && progress.total > 0 && (
        <div className="h-1.5 rounded-full bg-bg overflow-hidden">
          <div
            className="h-full bg-warn transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** 库健康摘要条：计数水位 + 待治理事实（零参与/高匹配零消费/触发词碰撞）。 */
export function HealthStrip() {
  const { t } = useTranslation(["skills", "common"]);
  const { data: health } = useQuery({
    queryKey: ["skills", "health"],
    queryFn: () => skillsApi.health().then((r) => r.data),
    staleTime: 30_000, throwOnError: false,
  });
  if (!health) return null;
  const { counts, capacity_reference: ref, embedding } = health;
  const over = counts.active > ref;
  const items: string[] = [];
  if (health.zero_engagement.length > 0) {
    items.push(t("healthZeroEngagement", { count: health.zero_engagement.length }));
  }
  if (health.high_match_low_use.length > 0) {
    items.push(t("healthHighMatchLowUse", { count: health.high_match_low_use.length }));
  }
  const collisionCount = Object.keys(health.trigger_collisions).length;
  if (collisionCount > 0) {
    items.push(t("healthCollisions", { count: collisionCount }));
  }
  const parseErrorCount = Object.keys(health.parse_errors || {}).length;
  if (parseErrorCount > 0) {
    items.push(t("healthParseErrors", { count: parseErrorCount }));
  }
  const embeddingIncomplete = embedding && embedding.embedded < embedding.total;
  return (
    <div className="flex items-center gap-3 flex-wrap text-xs text-muted px-4 py-2 rounded-md border border-border bg-card">
      <Activity size={14} className={over ? "text-warn" : "text-ok"} />
      <span>
        {t("healthCapacity", { active: counts.active, stale: counts.stale, ref })}
        {over && <span className="text-warn"> · {t("healthOverCapacity")}</span>}
      </span>
      {embedding && (
        <span className={embedding.rebuilding || embeddingIncomplete ? "text-warn" : ""}>
          {embedding.rebuilding
            ? t("healthRebuilding", { embedded: embedding.embedded, total: embedding.total })
            : t("healthEmbedding", { embedded: embedding.embedded, total: embedding.total, model: embedding.model || t("embeddingNoModel") })}
        </span>
      )}
      {items.length > 0 && <span className="text-warn">· {items.join(" · ")}</span>}
      {items.length === 0 && !over && !embeddingIncomplete && <span>{t("healthClean")}</span>}
    </div>
  );
}
