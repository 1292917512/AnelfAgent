import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FlaskConical } from "lucide-react";
import { configMetaApi, judgmentApi } from "@/lib/api";
import type { ConfigMetaItem, JudgmentChannel, JudgmentTestResult } from "@/lib/types";
import { Badge, Button, type BadgeVariant } from "@/components/ui";
import { ConfigItemRow } from "@/pages/config/ConfigItemRow";
import { ConfigDetailDrawer } from "@/pages/config/ConfigDetailDrawer";

/** 判断能力的统一配置组（与后端 agent/judgment/configs.py 对应） */
const CONFIG_GROUP = "judgment/core";

const CHANNEL_VARIANT: Record<JudgmentChannel, BadgeVariant> = {
  typesafe: "ok",
  llm_fallback: "warn",
  disabled: "neutral",
  unavailable: "danger",
};

/** 通道测试执行结果卡片。 */
function TestResultCard({ result }: { result: JudgmentTestResult }) {
  const { t } = useTranslation("models");
  if (!result.ok) {
    return (
      <div className="rounded-md border border-danger/30 bg-danger-subtle p-3 text-sm">
        <div className="text-danger font-medium">{t("judgment.testFailed")}</div>
        <div className="text-xs text-muted mt-1">
          {result.error}
          {result.cause ? ` (${result.cause})` : ""}
        </div>
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 text-xs">
        <Badge variant={result.source === "typesafe" ? "ok" : "warn"}>
          {result.source === "typesafe" ? t("judgment.sourceTypesafe") : t("judgment.sourceFallback")}
        </Badge>
        {result.model && <span className="text-muted font-mono">{result.model}</span>}
        <span className="text-muted">{result.latency_ms}ms</span>
        {result.usage && (
          <span className="text-muted">
            {t("judgment.tokens", { input: result.usage.input_tokens, output: result.usage.output_tokens })}
          </span>
        )}
      </div>
      <pre className="text-xs font-mono text-foreground bg-bg rounded-md p-3 overflow-x-auto whitespace-pre-wrap">
        {JSON.stringify(result.answers, null, 2)}
      </pre>
      {result.missing && result.missing.length > 0 && (
        <div className="text-xs text-warn">{t("judgment.missing", { ids: result.missing.join(", ") })}</div>
      )}
    </div>
  );
}

/** Jev 判断通道面板：通道状态与连通性测试 + 统一配置面（judgment/core）配置项。 */
export function JudgmentPanel() {
  const { t } = useTranslation("models");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [detailItem, setDetailItem] = useState<ConfigMetaItem | null>(null);

  const statusQuery = useQuery({
    queryKey: ["judgmentStatus"],
    queryFn: () => judgmentApi.status().then((r) => r.data),
  });
  const configQuery = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
  });
  const testMutation = useMutation({
    mutationFn: () => judgmentApi.test().then((r) => r.data),
  });

  const status = statusQuery.data;
  const items = configQuery.data?.groups.find((g) => g.group === CONFIG_GROUP)?.items ?? [];
  const basicItems = items.filter((item) => !item.advanced);
  const advancedItems = items.filter((item) => item.advanced);

  return (
    <div className="space-y-4">
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
        {status && (
          <div className="text-xs text-muted font-mono break-all">
            {status.channel === "typesafe"
              ? `${status.base_url} · ${status.model}`
              : status.channel === "llm_fallback"
                ? status.fallback_model || t("judgment.defaultChain")
                : t(`judgment.channel.${status.channel}`)}
            {` · ${t("judgment.timeout", { seconds: status.timeout })}`}
          </div>
        )}
        {testMutation.data && <TestResultCard result={testMutation.data} />}
        {testMutation.isError && (
          <div className="text-xs text-danger">{testMutation.error.message}</div>
        )}
      </div>

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
