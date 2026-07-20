import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Database } from "lucide-react";
import { configApi } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { Card } from "@/components/common/Card";
import { Badge } from "@/components/ui";
import { InfoRow } from "./shared";

/** 配置文件状态 + Mind 配置 + 关于 */
export function ConfigStatusPanel() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const branding = useAppStore((s) => s.branding);
  const { data: snapshot } = useQuery({
    queryKey: ["configSnapshot"],
    queryFn: () => configApi.snapshot().then((r) => r.data),
  });

  const configItems: { key: string; file: string }[] = [
    { key: "app", file: "app_config.json" },
    { key: "mind", file: "mind_config.json" },
    { key: "llm", file: "llm_clients.json" },
    { key: "mcp", file: "mcp_servers.json" },
    { key: "personas", file: "personas.json" },
  ];

  const mindConfig = snapshot?.mind as Record<string, unknown> | undefined;

  return (
    <div className="space-y-4">
      <Card title={t("configFileStatus")}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {configItems.map((item) => (
            <div
              key={item.key}
              className="flex items-center gap-3 p-3 rounded-md bg-elevated border border-border"
            >
              <Database size={16} className={snapshot?.[item.key] ? "text-ok" : "text-muted"} />
              <div className="min-w-0">
                <p className="text-sm font-medium text-heading">{t(`configLabels.${item.key}`)}</p>
                <p className="text-xs text-muted font-mono truncate">{item.file}</p>
              </div>
              <Badge variant={snapshot?.[item.key] ? "ok" : "neutral"} className="ml-auto">
                {snapshot?.[item.key] ? tc("loaded") : tc("notFound")}
              </Badge>
            </div>
          ))}
        </div>
      </Card>

      {mindConfig && (
        <Card title={t("configLabels.mind")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(mindConfig)
              .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
              .map(([k, v]) => (
                <InfoRow key={k} label={t(`mindFields.${k}`, { defaultValue: k })} value={String(v)} />
              ))}
          </div>
        </Card>
      )}

      <Card title={t("about")}>
        <div className="space-y-2 text-sm">
          <p className="text-foreground">
            <span className="font-semibold text-heading">{branding.title}</span> — {branding.subtitle}
          </p>
          <p className="text-muted">{t("versionInfo", { version: branding.version })}</p>
          <p className="text-muted">{t("webuiInfo")}</p>
        </div>
      </Card>
    </div>
  );
}
