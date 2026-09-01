/** 能力 × 提供者矩阵面板：每个能力的实现切换 + 提供者卡片列表。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check } from "lucide-react";
import { webEntityApi } from "./api";
import { cn } from "@/lib/utils";
import { ProviderCard } from "./ProviderCard";

export function ProvidersPanel() {
  const { t } = useTranslation("web");
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["webProvidersMatrix"],
    queryFn: () => webEntityApi.matrix().then((r) => r.data),
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["webProvidersMatrix"] });

  const activeMutation = useMutation({
    mutationFn: ({ capability, provider }: { capability: string; provider: string }) =>
      webEntityApi.setActive(capability, provider),
    onSuccess: invalidate,
  });

  if (!data) return null;

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="rounded-lg border border-border bg-card divide-y divide-border">
        {data.capabilities.map((cap) => {
          const capable = data.providers.filter((p) => p.capabilities.includes(cap));
          const selection = data.selection[cap] ?? "auto";
          const active = data.active[cap];
          return (
            <div key={cap} className="flex items-center gap-3 px-4 py-3">
              <div className="w-20 shrink-0 text-sm font-medium text-heading">{t(`caps.${cap}`)}</div>
              <div className="flex items-center gap-1 flex-wrap">
                {["auto", ...capable.map((p) => p.name)].map((name) => {
                  const selected = selection === name;
                  return (
                    <button
                      key={name}
                      onClick={() => activeMutation.mutate({ capability: cap, provider: name })}
                      disabled={activeMutation.isPending}
                      className={cn(
                        "px-2.5 py-1 rounded-md border text-xs transition-colors disabled:opacity-50",
                        selected
                          ? "border-accent bg-accent/10 text-accent"
                          : "border-border text-muted hover:text-foreground hover:border-border-strong",
                      )}
                    >
                      {name === "auto"
                        ? t("matrix.auto")
                        : data.providers.find((p) => p.name === name)?.display_name ?? name}
                    </button>
                  );
                })}
              </div>
              <div className="ml-auto text-xs text-muted shrink-0">
                {active
                  ? `${t("matrix.activeNow")}: ${data.providers.find((p) => p.name === active)?.display_name ?? active}`
                  : t("matrix.noneAvailable")}
                {selection === "auto" && active && <Check size={12} className="inline ml-1 text-accent" />}
              </div>
            </div>
          );
        })}
      </div>

      {data.providers.map((provider) => (
        <ProviderCard key={provider.name} provider={provider} onChanged={invalidate} />
      ))}
    </div>
  );
}
