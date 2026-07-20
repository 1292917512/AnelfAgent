import { useState, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { nonebotApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Save,
  Plug,
  Bot,
  Package,
  Circle,
} from "lucide-react";

interface NbAdapter {
  key: string;
  label: string;
  package: string;
  installed: boolean;
  registered: boolean;
}

interface NbBot {
  self_id: string;
  adapter: string;
}

export function NoneBotPanel() {
  const { t } = useTranslation("channels");
  const queryClient = useQueryClient();

  const { data: statusData } = useQuery({
    queryKey: ["nonebot-status"],
    queryFn: () => nonebotApi.status().then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: adaptersData } = useQuery({
    queryKey: ["nonebot-adapters"],
    queryFn: () => nonebotApi.adapters().then((r) => r.data),
  });

  const { data: botsData } = useQuery({
    queryKey: ["nonebot-bots"],
    queryFn: () => nonebotApi.bots().then((r) => r.data),
    refetchInterval: 5000,
  });

  const { data: configData } = useQuery({
    queryKey: ["nonebot-config"],
    queryFn: () => nonebotApi.config().then((r) => r.data),
  });

  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [configDirty, setConfigDirty] = useState(false);

  useEffect(() => {
    if (configData) {
      setConfigValues(configData);
      setConfigDirty(false);
    }
  }, [configData]);

  const saveMutation = useMutation({
    mutationFn: (vals: Record<string, unknown>) => nonebotApi.saveConfig(vals),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["nonebot-config"] });
      setConfigDirty(false);
    },
  });

  const initialized = statusData?.initialized ?? false;
  const registeredAdapters: string[] = statusData?.registered_adapters ?? [];
  const adapters: NbAdapter[] = adaptersData?.adapters ?? [];
  const bots: NbBot[] = botsData?.bots ?? [];

  const updateConfig = (key: string, val: unknown) => {
    setConfigValues((prev) => ({ ...prev, [key]: val }));
    setConfigDirty(true);
  };

  const selectedAdapters: string[] = (configValues.adapters as string[]) ?? [];

  const toggleAdapterSelection = (key: string) => {
    const current = [...selectedAdapters];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    updateConfig("adapters", current);
  };

  return (
    <div className="space-y-4">
      {/* Status */}
      <div className={cn(
        "flex items-center gap-3 px-4 py-3 rounded-md border text-sm",
        initialized
          ? "bg-ok-subtle text-ok border-[rgba(34,197,94,0.2)]"
          : "bg-secondary text-muted border-border",
      )}>
        <Plug size={16} />
        <span>{initialized ? t("nonebotInitialized") : t("nonebotNotStarted")}</span>
        {registeredAdapters.length > 0 && (
          <span className="text-xs opacity-75 ml-2">
            {t("registered")}: {registeredAdapters.join(", ")}
          </span>
        )}
      </div>

      {/* Online Bots */}
      {bots.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-xs font-semibold text-muted uppercase tracking-wider">
            {t("onlineBots")}
          </h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {bots.map((bot) => (
              <div key={bot.self_id}
                className="flex items-center gap-2 px-3 py-2 rounded-md border border-border bg-card">
                <Bot size={14} className="text-accent" />
                <span className="text-sm font-mono text-foreground">{bot.self_id}</span>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted ml-auto">
                  {bot.adapter}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Adapter Selection */}
      <div className="space-y-2">
        <h4 className="text-xs font-semibold text-muted uppercase tracking-wider">
          {t("availableAdapters")}
        </h4>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {adapters.map((adapter) => (
            <button key={adapter.key}
              onClick={() => toggleAdapterSelection(adapter.key)}
              className={cn(
                "flex items-center gap-2 px-3 py-2.5 rounded-md border text-left transition-all text-sm",
                selectedAdapters.includes(adapter.key)
                  ? "border-accent bg-accent/5"
                  : "border-border hover:border-border-strong",
                !adapter.installed && "opacity-50",
              )}>
              <Circle size={8}
                className={cn(
                  "shrink-0",
                  adapter.registered ? "fill-[var(--ok)] text-ok"
                    : selectedAdapters.includes(adapter.key) ? "fill-[var(--accent)] text-accent"
                    : "text-muted",
                )} />
              <div className="flex-1 min-w-0">
                <span className="text-heading font-medium">{adapter.label}</span>
                <div className="flex items-center gap-1 mt-0.5">
                  <Package size={10} className="text-muted" />
                  <span className="text-[10px] text-muted truncate">{adapter.package}</span>
                </div>
              </div>
              {!adapter.installed && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-warn-subtle text-warn border border-[rgba(245,158,11,0.2)] shrink-0">
                  {t("notInstalled")}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Config */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-muted uppercase tracking-wider">
            {t("bridgeConfig")}
          </h4>
          {configDirty && (
            <button
              onClick={() => saveMutation.mutate(configValues)}
              disabled={saveMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md
                bg-accent text-white hover:opacity-90 transition-all disabled:opacity-50">
              <Save size={14} />
              {saveMutation.isPending ? t("common:saving") : t("common:save")}
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-heading">{t("enableBridge")}</label>
            <div className="flex items-center gap-2">
              <button onClick={() => updateConfig("enabled", !configValues.enabled)}
                className={cn(
                  "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                  configValues.enabled ? "bg-accent" : "bg-secondary border border-border",
                )}>
                <span className={cn(
                  "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform",
                  configValues.enabled ? "translate-x-[18px]" : "translate-x-[3px]",
                )} />
              </button>
              <span className="text-xs text-muted">
                {configValues.enabled ? t("common:enabled") : t("common:disabled")}
              </span>
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-heading">{t("interceptMode")}</label>
            <p className="text-[11px] text-muted">{t("interceptDesc")}</p>
            <div className="flex items-center gap-2">
              <button onClick={() => updateConfig("intercept_all", !configValues.intercept_all)}
                className={cn(
                  "relative inline-flex h-5 w-9 items-center rounded-full transition-colors",
                  configValues.intercept_all ? "bg-accent" : "bg-secondary border border-border",
                )}>
                <span className={cn(
                  "inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform",
                  configValues.intercept_all ? "translate-x-[18px]" : "translate-x-[3px]",
                )} />
              </button>
              <span className="text-xs text-muted">
                {configValues.intercept_all ? t("fullIntercept") : t("passthrough")}
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
