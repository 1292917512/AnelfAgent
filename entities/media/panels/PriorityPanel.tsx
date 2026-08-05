/** 媒体库能力优先级面板：各能力的 provider 链排序与可用状态。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUp, Save } from "lucide-react";
import { mediaApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import type { MediaProviderStatus } from "@/lib/types";

const CAP_ORDER = [
  "vision", "asr", "tts", "voice_mgmt", "music",
  "video", "image_gen", "image_edit", "rerank",
];

export function PriorityPanel() {
  const { t } = useTranslation("media");
  const queryClient = useQueryClient();
  const [chains, setChains] = useState<Record<string, string[]>>({});
  const [dirty, setDirty] = useState(false);

  const { data } = useQuery({
    queryKey: ["media-providers"],
    queryFn: () => mediaApi.providers().then((r) => r.data),
  });

  useEffect(() => {
    if (data && !dirty) {
      setChains(data.provider_priority ?? {});
    }
  }, [data, dirty]);

  const providers: MediaProviderStatus[] = data?.providers ?? [];
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
    mutationFn: () => mediaApi.updateConfig({ provider_priority: chains }),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["media-providers"] });
      queryClient.invalidateQueries({ queryKey: ["media-config"] });
    },
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <p className="text-xs text-muted">{t("priority.hint")}</p>
      {CAP_ORDER.map((cap) => {
        const chain = chains[cap] ?? [];
        if (chain.length === 0) return null;
        return (
          <Card key={cap} title={t(`caps.${cap}`)}>
            <div className="space-y-1.5">
              {chain.map((name, index) => {
                const ready = configuredOf(name, cap);
                return (
                  <div
                    key={name}
                    className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-md border border-border bg-card"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="w-5 h-5 flex items-center justify-center rounded-full bg-secondary text-[10px] font-bold text-muted shrink-0">
                        {index + 1}
                      </span>
                      <span className="text-xs font-medium text-heading">{t(`providers.${name}`)}</span>
                      <StatusDot status={ready ? "ok" : "danger"} />
                      <span className="text-[10px] text-muted">
                        {ready ? t("priority.configured") : t("priority.notConfigured")}
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
                );
              })}
            </div>
          </Card>
        );
      })}
      <button
        onClick={() => saveMutation.mutate()}
        disabled={!dirty || saveMutation.isPending}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
      >
        <Save size={12} />
        {t("common.save")}
      </button>
    </div>
  );
}
