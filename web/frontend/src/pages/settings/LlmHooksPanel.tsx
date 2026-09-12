import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Zap } from "lucide-react";
import { hooksLlmApi, type LlmHookItem } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Badge, EmptyState, LoadingBlock } from "@/components/ui";

const SOURCE_VARIANT: Record<string, "accent" | "info" | "warn" | "neutral"> = {
  code: "accent",
  task: "info",
  entity: "warn",
};

/** LLM 钩子面观测面板：全部已注册钩子 + 治理配置（只读；开关经配置中心热调） */
export function LlmHooksPanel() {
  const { t } = useTranslation("settings");
  const { data, isLoading } = useQuery({
    queryKey: ["hooks-llm"],
    queryFn: () => hooksLlmApi.get().then((r) => r.data),
    refetchInterval: 8000,
  });

  if (isLoading || !data) return <LoadingBlock label={t("common:loading")} />;

  return (
    <Card
      title={t("llmHooks.title")}
      subtitle={t("llmHooks.subtitle")}
      actions={
        <div className="flex items-center gap-2">
          <Badge variant={data.enabled ? "ok" : "neutral"}>
            {data.enabled ? t("llmHooks.enabled") : t("llmHooks.disabled")}
          </Badge>
          <Badge variant={data.runtime_started ? "info" : "neutral"}>
            {data.runtime_started ? t("llmHooks.running") : t("llmHooks.stopped")}
          </Badge>
        </div>
      }
    >
      <div className="mb-4 flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-muted">
        <span>{t("llmHooks.govConcurrent")}: {data.governance.max_concurrent}</span>
        <span>
          {t("llmHooks.govTranscript")}: {data.governance.transcript_enabled
            ? t("llmHooks.transcriptOn", { chars: data.governance.transcript_max_chars })
            : t("llmHooks.transcriptOff")}
        </span>
        <span>{t("llmHooks.eventsLabel")}: {data.events.join(" / ")}</span>
      </div>

      {data.hooks.length === 0 ? (
        <EmptyState icon={Zap} title={t("llmHooks.noHooks")} />
      ) : (
        <ul className="space-y-2">
          {data.hooks.map((hook) => (
            <HookRow key={hook.name} hook={hook} />
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-muted">{t("llmHooks.hint")}</p>
    </Card>
  );
}

function HookRow({ hook }: { hook: LlmHookItem }) {
  const { t } = useTranslation("settings");
  return (
    <li className="rounded-md border border-border bg-elevated p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">{hook.name}</span>
        <span className="font-mono text-[10px] text-muted">{hook.event}</span>
        <Badge variant="neutral">{hook.context}</Badge>
        <Badge variant={SOURCE_VARIANT[hook.source] ?? "neutral"}>
          {t(`llmHooks.source.${hook.source}`, { defaultValue: hook.source })}
        </Badge>
        {hook.model && <Badge variant="info">{hook.model}</Badge>}
      </div>
      {hook.description && (
        <p className="mt-1 text-xs text-muted">{hook.description}</p>
      )}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-[10px] text-muted">
        <span>{t("llmHooks.iterations")}: {hook.max_iterations}</span>
        <span>{t("llmHooks.concurrent")}: {hook.max_concurrent}</span>
        {hook.cooldown_seconds > 0 && (
          <span>{t("llmHooks.cooldown")}: {hook.cooldown_seconds}s</span>
        )}
        {hook.debounce_seconds > 0 && (
          <span>{t("llmHooks.debounce")}: {hook.debounce_seconds}s</span>
        )}
        {hook.tool_tags.length > 0 && (
          <span>{t("llmHooks.toolTags")}: {hook.tool_tags.join(", ")}</span>
        )}
        {hook.allow_output_tools && <span>{t("llmHooks.outputAllowed")}</span>}
        <span className="ml-auto">{t("llmHooks.owner")}: {hook.owner}</span>
      </div>
    </li>
  );
}
