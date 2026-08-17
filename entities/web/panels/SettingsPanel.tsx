/** 通用设置面板：抓取代理 + 安全状态（SSRF 开关在配置中心调整）。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save } from "lucide-react";
import { configApi, entitiesApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";

export function SettingsPanel() {
  const { t } = useTranslation("web");
  const queryClient = useQueryClient();
  const [proxy, setProxy] = useState("");

  const { data: webTools } = useQuery({
    queryKey: ["webToolsConfig"],
    queryFn: () => configApi.getWebTools().then((r) => r.data),
  });

  const { data: entityConfig } = useQuery({
    queryKey: ["entity-config", "web"],
    queryFn: () => entitiesApi.config("web").then((r) => r.data),
  });
  const values = (entityConfig as Record<string, unknown>)?.values as Record<string, unknown> | undefined;

  const saveMutation = useMutation({
    mutationFn: () => configApi.saveWebTools({ proxy: proxy || webTools?.proxy || "" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["webToolsConfig"] }),
  });

  return (
    <div className="space-y-4 max-w-2xl">
      <Card title={t("settings.proxyTitle")}>
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">
              {t("settings.proxyLabel")}
            </label>
            <input
              type="text"
              value={proxy || String(webTools?.proxy ?? "")}
              onChange={(e) => setProxy(e.target.value)}
              placeholder={t("settings.proxyPlaceholder")}
              className="w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
            />
          </div>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={saveMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
          >
            <Save size={12} />
            {saveMutation.isPending ? t("settings.saving") : t("settings.save")}
          </button>
        </div>
      </Card>

      <Card title={t("settings.securityTitle")}>
        <div className="flex items-center gap-2 text-xs">
          <StatusDot status="ok" />
          <span className="text-foreground">{t("settings.ssrf")}</span>
          <span className="text-muted">
            {String(values?.web_ssrf_protection ?? "true") === "true"
              ? t("settings.ssrfOn")
              : t("settings.ssrfOff")}
            {" · "}
            {t("settings.ssrfHint")}
          </span>
        </div>
      </Card>
    </div>
  );
}
