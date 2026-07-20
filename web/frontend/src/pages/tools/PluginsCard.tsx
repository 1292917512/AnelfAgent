import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui";
import type { PluginInfo } from "./types";

/** 已加载插件列表 */
export function PluginsCard({ plugins }: { plugins: PluginInfo[] }) {
  const { t } = useTranslation("tools");
  if (plugins.length === 0) return null;

  return (
    <div className="rounded-md border border-border bg-card overflow-hidden">
      <div className="px-4 py-3 border-b border-border">
        <span className="text-sm font-semibold text-heading">{t("pluginsTitle")}</span>
        <span className="ml-2 text-[11px] text-muted">
          {t("pluginsLoaded", { count: plugins.length })}
        </span>
      </div>
      <div>
        {plugins.map((p, idx) => (
          <div
            key={p.name}
            className={cn(
              "flex items-center justify-between gap-2 px-4 py-2.5",
              idx < plugins.length - 1 && "border-b border-border",
            )}
          >
            <div className="min-w-0">
              <span className="font-medium text-sm text-heading">{p.name}</span>
              <span className="ml-2 text-xs text-muted">v{p.version}</span>
              {p.description && (
                <p className="text-[11px] text-muted mt-0.5">{p.description}</p>
              )}
            </div>
            <Badge variant={p.enabled ? "ok" : "neutral"}>
              {p.enabled ? t("pluginEnabled") : t("pluginDisabled")}
            </Badge>
          </div>
        ))}
      </div>
    </div>
  );
}
