import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import { configMetaApi, judgmentApi } from "@/lib/api";
import type { ConfigMetaItem, JudgmentChannel } from "@/lib/types";
import { Badge, Button, type BadgeVariant } from "@/components/ui";
import { ConfigItemRow } from "@/pages/config/ConfigItemRow";
import { JudgmentTestResultCard } from "./judgment/JudgmentTestResultCard";
import { JudgmentProviders } from "./judgment/JudgmentProviders";
import { ConfigDetailDrawer } from "@/pages/config/ConfigDetailDrawer";

/** 判断能力的统一配置组（与后端 agent/judgment/configs.py 对应） */
const CONFIG_GROUP = "judgment/core";

const CHANNEL_VARIANT: Record<JudgmentChannel, BadgeVariant> = {
  typesafe: "ok",
  llm_fallback: "warn",
  disabled: "neutral",
  unavailable: "danger",
};

/** 服务状态与测速结果绑定当前配置，切换连接后重新获取。 */
function JudgmentConnection({ revision }: { revision: string }) {
  const { t } = useTranslation("models");
  const cache = useQueryClient();
  const statusQuery = useQuery({
    queryKey: ["judgmentStatus", revision],
    queryFn: () => judgmentApi.status().then((r) => r.data),
  });
  const testMutation = useMutation({
    mutationFn: () => judgmentApi.test().then((r) => r.data),
    onSettled: () => cache.invalidateQueries({ queryKey: ["judgmentCatalog"] }),
  });

  const status = statusQuery.data;

  return (
      <div className="rounded-md border border-border bg-card p-4 space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-sm text-heading font-medium">{t("judgment.channelStatus")}</span>
            {status && (
              <Badge variant={CHANNEL_VARIANT[status.channel]}>
                {t(`judgment.channel.${status.channel}`)}
              </Badge>
            )}
            {status?.api_key_configured && (
              <Badge variant="info">{t("judgment.keyConfigured")}</Badge>
            )}
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => testMutation.mutate()}
            loading={testMutation.isPending}
            disabled={!status || status.channel === "disabled" || status.channel === "unavailable"}
          >
            <FlaskConical size={13} />
            {t("judgment.testChannel")}
          </Button>
        </div>
        <p className="text-xs leading-relaxed text-muted">{t("judgment.testHint")}</p>
        {status?.error && <p role="alert" className="text-sm text-danger">{status.error}</p>}
        {statusQuery.isError && <p role="alert" className="text-sm text-danger">{statusQuery.error.message}</p>}
        {status && !status.error && (
          <div className="text-xs text-muted font-mono break-all">
            {status.channel === "typesafe"
              ? `${status.base_url} · ${status.model}`
              : status.channel === "llm_fallback"
                ? status.fallback_model || t("judgment.defaultChain")
                : t(`judgment.channel.${status.channel}`)}
            {` · ${t("judgment.timeout", { seconds: status.timeout })}`}
          </div>
        )}
        {testMutation.data && <JudgmentTestResultCard result={testMutation.data} />}
        {testMutation.isError && (
          <div className="text-xs text-danger">{testMutation.error.message}</div>
        )}
      </div>
  );
}

/** Jev 服务连接与策略配置，字段由统一配置元数据驱动。 */
export function JudgmentPanel() {
  const { t } = useTranslation("models");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [detailItem, setDetailItem] = useState<ConfigMetaItem | null>(null);
  const configQuery = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });
  const items = configQuery.data?.groups.find((g) => g.group === CONFIG_GROUP)?.items ?? [];
  const basicItems = items.filter((item) => !item.advanced);
  const advancedItems = items.filter((item) => item.advanced);
  const catalogQuery = useQuery({ queryKey: ["judgmentCatalog"], queryFn: () => judgmentApi.catalog().then((r) => r.data), refetchInterval: 5000 });
  const revision = `${configQuery.dataUpdatedAt}:${catalogQuery.data?.revision ?? "0"}`;

  return (
    <div className="space-y-4">
      <JudgmentProviders catalog={catalogQuery.data} error={catalogQuery.error} revision={revision} />
      {configQuery.isSuccess && <JudgmentConnection key={revision} revision={revision} />}

      <div className="space-y-2">
        {basicItems.map((item) => (
          <ConfigItemRow key={item.key} item={item} onOpenDetail={setDetailItem} />
        ))}
        {advancedItems.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setShowAdvanced((v) => !v)}
              className="inline-flex items-center gap-1 text-xs text-muted hover:text-foreground transition-colors"
            >
              {showAdvanced ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              {t("judgment.advanced", { count: advancedItems.length })}
            </button>
            {showAdvanced &&
              advancedItems.map((item) => (
                <ConfigItemRow key={item.key} item={item} onOpenDetail={setDetailItem} />
              ))}
          </>
        )}
      </div>

      <ConfigDetailDrawer item={detailItem} group={CONFIG_GROUP} onClose={() => setDetailItem(null)} />
    </div>
  );
}
