/** 能力优先级链编辑面板（视觉/声音能力页共用）。
 *
 * 从能力路由状态（生效链 + 各提供者配置状态）出发编辑顺序，
 * 保存写回对应的 provider_priority 配置键（JSON 字典）。
 */
import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Save } from "lucide-react";
import { configMetaApi, type CapabilityStatus } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";

export function CapabilityChainPanel({
  status,
  caps,
  configKey,
  ns,
  queryKey,
}: {
  status: CapabilityStatus;
  caps: string[];
  /** provider_priority 配置键（如 vision_provider_priority） */
  configKey: string;
  /** 能力名翻译的 i18n 命名空间（t(`caps.<cap>`)） */
  ns: string;
  /** 保存后失效的查询键 */
  queryKey: string;
}) {
  const { t } = useTranslation(ns);
  const queryClient = useQueryClient();
  const [chains, setChains] = useState<Record<string, string[]>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) setChains(status.chains ?? {});
  }, [status, dirty]);

  const providers = status.providers ?? [];
  const configuredOf = (name: string, cap: string): boolean => {
    const p = providers.find((item) => item.name === name);
    return p ? !!p.configured?.[cap] : false;
  };

  const move = (cap: string, index: number, delta: number) => {
    setChains((prev) => {
      const chain = [...(prev[cap] ?? [])];
      const target = index + delta;
      const current = chain[index];
      const swap = chain[target];
      if (current === undefined || swap === undefined) return prev;
      chain[index] = swap;
      chain[target] = current;
      return { ...prev, [cap]: chain };
    });
    setDirty(true);
  };

  const saveMutation = useMutation({
    mutationFn: () => configMetaApi.save(configKey, chains),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: [queryKey] });
    },
  });

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted">{t("chains.hint")}</p>
      {caps.map((cap) => {
        const chain = chains[cap] ?? [];
        if (chain.length === 0) return null;
        return (
          <Card key={cap} title={t(`caps.${cap}`)}>
            <div className="space-y-1.5">
              {chain.map((name, index) => {
                const ready = configuredOf(name, cap);
                const detail = providers.find((item) => item.name === name)?.details?.[cap];
                const videoModels = detail?.video_models as string[] | undefined;
                return (
                  <div
                    key={name}
                    className="px-2.5 py-1.5 rounded-md border border-border bg-card"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="w-5 h-5 flex items-center justify-center rounded-full bg-secondary text-[10px] font-bold text-muted shrink-0">
                          {index + 1}
                        </span>
                        <span className="text-xs font-medium text-heading">{t(`providers.${name}`, { defaultValue: name })}</span>
                        <StatusDot status={ready ? "ok" : "danger"} />
                        <span className="text-[10px] text-muted">
                          {ready ? t("chains.configured") : t("chains.notConfigured")}
                        </span>
                      </div>
                      <div className="flex items-center gap-1 shrink-0">
                        <button
                          onClick={() => move(cap, index, -1)}
                          disabled={index === 0}
                          className="p-1 rounded text-muted hover:text-foreground disabled:opacity-30"
                        >
                          <ArrowUp size={13} />
                        </button>
                        <button
                          onClick={() => move(cap, index, 1)}
                          disabled={index === chain.length - 1}
                          className="p-1 rounded text-muted hover:text-foreground disabled:opacity-30"
                        >
                          <ArrowDown size={13} />
                        </button>
                      </div>
                    </div>
                    {videoModels !== undefined && (
                      <p className="pl-7 pt-1 text-[10px] text-muted">
                        {t("chains.videoModels")}:{" "}
                        {videoModels.length ? videoModels.join("、") : t("chains.noVideoModels")}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>
        );
      })}
      {dirty && (
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          <Save size={13} />
          {saveMutation.isPending ? t("chains.saving") : t("chains.save")}
        </button>
      )}
    </div>
  );
}
