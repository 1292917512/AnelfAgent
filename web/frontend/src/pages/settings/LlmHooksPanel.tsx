import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Zap } from "lucide-react";
import { hooksLlmApi } from "@/lib/api";
import type { LlmHookItem } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Badge, EmptyState, LoadingBlock, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";

const SOURCE_VARIANT: Record<string, "accent" | "info" | "warn" | "neutral"> = {
  code: "accent",
  task: "info",
  entity: "warn",
};

/** LLM 钩子面观测面板：全部已注册钩子 + 治理配置 + 运行期启停（注册表内存态）。 */
export function LlmHooksPanel() {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["hooks-llm"],
    queryFn: () => hooksLlmApi.get().then((r) => r.data),
    refetchInterval: 8000,
  });

  const toggle = useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      hooksLlmApi.setEnabled(name, enabled),
    onSuccess: (_, vars) => {
      toast.success(t("llmHooks.toggleSaved", { name: vars.name }));
      queryClient.invalidateQueries({ queryKey: ["hooks-llm"] });
    },
    onError: (exc) => {
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("llmHooks.toggleSaveFailed"));
    },
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
            <HookRow
              key={hook.name}
              hook={hook}
              onToggle={(enabled) => toggle.mutate({ name: hook.name, enabled })}
              toggling={toggle.isPending}
            />
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-muted">{t("llmHooks.hint")}</p>
    </Card>
  );
}

function HookRow({ hook, onToggle, toggling }: {
  hook: LlmHookItem;
  onToggle: (enabled: boolean) => void;
  toggling: boolean;
}) {
  const { t } = useTranslation("settings");
  return (
    <li className={`rounded-lg border border-border bg-elevated/60 px-3 py-2.5 transition-colors ${hook.enabled ? "" : "opacity-55"}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Switch
          checked={hook.enabled}
          label={t("llmHooks.rowToggle")}
          disabled={toggling}
          onChange={onToggle}
        />
        <span className="text-sm font-medium text-foreground">{hook.name}</span>
        <span className="font-mono text-[10px] text-muted">{hook.event}</span>
        <Badge variant="neutral">{hook.context}</Badge>
        <Badge variant={SOURCE_VARIANT[hook.source] ?? "neutral"}>
          {t(`llmHooks.source.${hook.source}`, { defaultValue: hook.source })}
        </Badge>
        {!hook.enabled && <Badge variant="neutral">{t("llmHooks.rowDisabled")}</Badge>}
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
        {!hook.route_output && <span>{t("llmHooks.outputNotRouted")}</span>}
        <span className="ml-auto">{t("llmHooks.owner")}: {hook.owner}</span>
      </div>
    </li>
  );
}
