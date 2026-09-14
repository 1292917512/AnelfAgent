/** 抓取设置面板：代理与 SSRF 防护开关（保存即生效）。 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { retrievalApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Switch } from "@/components/ui";

export function RetrievalSettingsPanel() {
  const { t } = useTranslation("retrieval");
  const queryClient = useQueryClient();
  const { data } = useQuery({
    queryKey: ["retrievalSettings"],
    queryFn: () => retrievalApi.settings().then((r) => r.data),
  });
  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["retrievalSettings"] });

  const proxyMutation = useMutation({
    mutationFn: (proxy: string) => retrievalApi.saveSettings({ proxy }),
    onSuccess: invalidate,
  });
  const ssrfMutation = useMutation({
    mutationFn: (enabled: boolean) => retrievalApi.saveSettings({ ssrf_protection: enabled }),
    onSuccess: invalidate,
  });

  if (!data) return null;

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title={t("settings.proxy")} subtitle={t("settings.proxySubtitle")}>
        <div className="flex items-center gap-2">
          <input
            type="text"
            key={data.proxy}
            defaultValue={data.proxy}
            placeholder={t("settings.proxyPlaceholder")}
            onBlur={(e) => {
              if (e.target.value !== data.proxy) proxyMutation.mutate(e.target.value.trim());
            }}
            className="flex-1 px-2.5 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
          />
        </div>
      </Card>
      <Card title={t("settings.ssrf")} subtitle={t("settings.ssrfSubtitle")}>
        <div className="flex items-center gap-2 text-xs text-foreground">
          <Switch
            checked={data.ssrf_protection}
            onChange={(v) => ssrfMutation.mutate(v)}
          />
          <span>{data.ssrf_protection ? t("settings.ssrfOn") : t("settings.ssrfOff")}</span>
        </div>
      </Card>
    </div>
  );
}
