import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adaptersApi } from "@/lib/api";
import type { AdapterInfo } from "@/lib/types";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import {
  Power,
  Save,
  ChevronDown,
  RotateCcw,
  CheckCircle,
  Settings2,
  Blocks,
  Wifi,
  WifiOff,
} from "lucide-react";
import { ChannelWebView } from "@/pages/channels/ChannelWebView";
import { ConfigField } from "@/pages/channels/ConfigField";
import type { ConfigMeta } from "@/pages/channels/ConfigField";

const GROUP_KEY_MAP: Record<string, string> = {
  telegram: "adapter/telegram",
  http_api: "adapter/http_api",
  qq: "adapter/qq",
  feishu: "adapter/feishu",
};

const HIDDEN_CHANNELS = new Set(["nonebot_bridge"]);

const statusToColor = (s: string): "ok" | "warn" | "danger" | "offline" => {
  switch (s) {
    case "running": return "ok";
    case "starting": case "reconnecting": return "warn";
    case "error": return "danger";
    default: return "offline";
  }
};

export function ChannelsPanel({
  onOpenTools,
}: {
  /** 打开频道接口抽屉（开关 / 测试该频道的接口） */
  onOpenTools?: (channel: { key: string; name: string }) => void;
}) {
  const { t } = useTranslation("channels");
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [togglingKey, setTogglingKey] = useState<string | null>(null);
  const togglingRef = useRef<{ key: string; prevStatus: string } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["adapters"],
    queryFn: () => adaptersApi.list().then((r) => r.data),
    refetchInterval: togglingKey ? 1000 : 5000,
  });

  const { data: rawConfigs } = useQuery({
    queryKey: ["adapterConfigs"],
    queryFn: () => adaptersApi.configs().then((r) => r.data as Record<string, ConfigMeta>),
  });

  const toggleMutation = useMutation({
    mutationFn: (key: string) => {
      const prev = (data?.adapters ?? []).find((a: AdapterInfo) => a.key === key);
      togglingRef.current = { key, prevStatus: prev?.status ?? "" };
      setTogglingKey(key);
      return adaptersApi.toggle(key);
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["adapters"] }); },
    onError: () => { setTogglingKey(null); togglingRef.current = null; },
  });

  useEffect(() => {
    if (!togglingRef.current || !data?.adapters) return;
    const { key, prevStatus } = togglingRef.current;
    const current = (data.adapters as AdapterInfo[]).find((a) => a.key === key);
    if (current && current.status !== prevStatus) {
      setTogglingKey(null);
      togglingRef.current = null;
    }
  }, [data]);

  const [values, setValues] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);
  const [saveOk, setSaveOk] = useState(false);

  useEffect(() => {
    if (!rawConfigs) return;
    const initial: Record<string, unknown> = {};
    for (const [key, meta] of Object.entries(rawConfigs)) {
      initial[key] = meta.value !== undefined ? meta.value : meta.default;
    }
    setValues(initial);
    setDirty(false);
  }, [rawConfigs]);

  const saveMutation = useMutation({
    mutationFn: (vals: Record<string, unknown>) => adaptersApi.saveConfigs(vals),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adapterConfigs"] });
      setDirty(false);
      setSaveOk(true);
      setTimeout(() => setSaveOk(false), 2000);
    },
  });

  const adapters: AdapterInfo[] = (data?.adapters ?? []).filter(
    (a: AdapterInfo) => !HIDDEN_CHANNELS.has(a.key),
  );
  const ready = data?.ready ?? false;

  const configGroups: Record<string, Array<[string, ConfigMeta]>> = {};
  if (rawConfigs) {
    for (const [key, meta] of Object.entries(rawConfigs)) {
      const g = meta.group || "other";
      if (!configGroups[g]) configGroups[g] = [];
      configGroups[g].push([key, meta]);
    }
  }

  const getConfigsForChannel = (channelKey: string): Array<[string, ConfigMeta]> => {
    const groupKey = GROUP_KEY_MAP[channelKey] ?? `adapter/${channelKey}`;
    return configGroups[groupKey] ?? [];
  };

  const updateVal = (key: string, val: unknown) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
    setSaveOk(false);
  };

  const allConfigGroups = Object.keys(configGroups);
  const adapterKeys = new Set(adapters.map((a) => a.key));
  const unmatchedGroups = allConfigGroups.filter((g) => {
    const channelKey = g.replace("adapter/", "");
    return !adapterKeys.has(channelKey) && !HIDDEN_CHANNELS.has(channelKey);
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end">
        {dirty && (
          <div className="flex items-center gap-2">
            {saveOk && (
              <span className="flex items-center gap-1 text-xs text-ok">
                <CheckCircle size={14} /> {t("savedOk")}
              </span>
            )}
            <button onClick={() => saveMutation.mutate(values)} disabled={saveMutation.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md
                bg-accent text-white hover:opacity-90 transition-all disabled:opacity-50">
              <Save size={14} />
              {saveMutation.isPending ? t("common:saving") : t("saveConfig")}
            </button>
          </div>
        )}
      </div>

      {isLoading ? (
        <p className="text-sm text-muted">{t("common:loading")}</p>
      ) : !ready ? (
        <p className="text-sm text-muted">{t("runtimeNotReady")}</p>
      ) : (
        <div className="grid gap-3">
          {adapters.map((a) => {
            const isOpen = expanded === a.key;
            const configs = getConfigsForChannel(a.key);
            return (
              <div key={a.key} className={cn(
                "rounded-md border transition-all bg-card",
                isOpen ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                       : "border-border hover:border-border-strong",
              )}>
                {/* Header */}
                <div className="flex items-center justify-between p-4 cursor-pointer"
                  onClick={() => setExpanded(isOpen ? null : a.key)}>
                  <div className="flex items-center gap-3">
                    <ChevronDown size={16} className={cn("text-muted transition-transform", isOpen && "rotate-180")} />
                    <StatusDot status={statusToColor(a.status)} />
                    <div>
                      <span className="text-sm font-medium text-heading">{a.name}</span>
                      <span className={cn("ml-2 text-[11px] px-2 py-0.5 rounded-full border",
                        a.status === "running"
                          ? "bg-ok-subtle text-ok border-[rgba(34,197,94,0.3)]"
                          : a.status === "error"
                            ? "bg-danger-subtle text-danger border-[rgba(239,68,68,0.3)]"
                            : "bg-secondary text-muted border-border"
                      )}>{a.status_display}</span>
                    </div>
                    {configs.length > 0 && (
                      <span className="text-[11px] text-muted flex items-center gap-1">
                        <Settings2 size={12} /> {t("nConfigItems", { count: configs.length })}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    {onOpenTools && (
                      <button onClick={() => onOpenTools({ key: a.key, name: a.name })}
                        title={t("tools.openTools")}
                        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border text-muted hover:text-foreground hover:bg-hover transition-all">
                        <Blocks size={14} />
                        {t("tools.openTools")}
                      </button>
                    )}
                    {(() => {
                      const isToggling = togglingKey === a.key;
                      const isRunning = a.status === "running";
                      return (
                        <button onClick={() => toggleMutation.mutate(a.key)} disabled={isToggling}
                          className={cn(
                            "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all disabled:opacity-70",
                            isToggling
                              ? "border-border text-warn bg-warn-subtle cursor-wait"
                              : isRunning
                                ? "border-[rgba(239,68,68,0.3)] text-danger hover:bg-danger-subtle"
                                : "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
                          )}>
                          <Power size={14} className={isToggling ? "animate-spin" : ""} />
                          {isToggling ? (isRunning ? t("stopping") : t("starting")) : isRunning ? t("stop") : t("start")}
                        </button>
                      );
                    })()}
                  </div>
                </div>

                {/* Expanded: status + config */}
                {isOpen && (
                  <div className="border-t border-border p-4 space-y-4">
                    {/* Connection status panel */}
                    {a.detail && (
                      <div className={cn(
                        "flex items-center gap-3 px-3 py-2.5 rounded-md text-xs font-mono",
                        (a.online || a.ws_connected)
                          ? "bg-ok-subtle text-ok border border-[rgba(34,197,94,0.2)]"
                          : a.status === "running" || a.status === "reconnecting"
                            ? "bg-warn-subtle text-warn border border-[rgba(245,158,11,0.2)]"
                            : "bg-secondary text-muted border border-border"
                      )}>
                        {(a.online || a.ws_connected) ? <Wifi size={14} /> : <WifiOff size={14} />}
                        <span>{a.detail}</span>
                        {a.self_id && (
                          <span className="ml-auto text-[10px] opacity-70">bot: {a.self_id}</span>
                        )}
                      </div>
                    )}

                    {/* Embedded WebUI iframe */}
                    <ChannelWebView channelKey={a.key} configs={configs} values={values} />

                    {configs.length > 0 ? (
                      <>
                        <div className="flex items-center justify-between">
                          <p className="text-xs font-semibold text-muted uppercase tracking-wider">
                            {t("channelConfig", { name: a.name })}
                          </p>
                          <button onClick={() => {
                            if (!rawConfigs) return;
                            const groupKey = GROUP_KEY_MAP[a.key] ?? `adapter/${a.key}`;
                            if (!groupKey) return;
                            const defaults: Record<string, unknown> = {};
                            for (const [k, m] of Object.entries(rawConfigs)) {
                              if (m.group === groupKey) defaults[k] = m.default;
                            }
                            setValues((prev) => ({ ...prev, ...defaults }));
                            setDirty(true); setSaveOk(false);
                          }}
                            className="flex items-center gap-1 px-2 py-1 text-[11px] text-muted rounded hover:bg-hover transition-colors">
                            <RotateCcw size={12} /> {t("resetDefaults")}
                          </button>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                          {configs
                            .filter(([, meta]) => {
                              if (!meta.tag) return true;
                              const modeKey = configs.find(([k]) => k.endsWith(".ws_mode"));
                              if (!modeKey) return true;
                              const currentMode = values[modeKey[0]] ?? modeKey[1].value;
                              return meta.tag === currentMode;
                            })
                            .map(([key, meta]) => (
                            <ConfigField key={key} configKey={key} meta={meta}
                              value={values[key]} onChange={(v) => updateVal(key, v)} />
                          ))}
                        </div>
                      </>
                    ) : (
                      <p className="text-sm text-muted text-center py-2">
                        {t("noConfig")}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {/* Unmatched config groups (not yet registered as channels) */}
          {unmatchedGroups.map((group) => {
            const channelKey = group.replace("adapter/", "");
            const configs = configGroups[group] ?? [];
            const isOpen = expanded === channelKey;
            const isToggling = togglingKey === channelKey;
            return (
              <div key={channelKey} className={cn(
                "rounded-md border transition-all bg-card",
                isOpen ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                       : "border-border hover:border-border-strong",
              )}>
                <div className="flex items-center justify-between p-4 cursor-pointer"
                  onClick={() => setExpanded(isOpen ? null : channelKey)}>
                  <div className="flex items-center gap-3">
                    <ChevronDown size={16} className={cn("text-muted transition-transform", isOpen && "rotate-180")} />
                    <StatusDot status="offline" />
                    <div>
                      <span className="text-sm font-medium text-heading">{channelKey}</span>
                      <span className="ml-2 text-[11px] px-2 py-0.5 rounded-full bg-secondary text-muted border border-border">
                        {t("notEnabled")}
                      </span>
                    </div>
                    <span className="text-[11px] text-muted flex items-center gap-1">
                      <Settings2 size={12} /> {t("nConfigItems", { count: configs.length })}
                    </span>
                  </div>
                  <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => {
                        togglingRef.current = { key: channelKey, prevStatus: "stopped" };
                        setTogglingKey(channelKey);
                        adaptersApi.toggle(channelKey).then(() => {
                          queryClient.invalidateQueries({ queryKey: ["adapters"] });
                        }).catch((e) => {
                          console.warn("[API]", e);
                          setTogglingKey(null);
                          togglingRef.current = null;
                        });
                      }}
                      disabled={isToggling}
                      className={cn(
                        "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-all disabled:opacity-70",
                        isToggling
                          ? "border-border text-warn bg-warn-subtle cursor-wait"
                          : "border-[rgba(34,197,94,0.3)] text-ok hover:bg-ok-subtle",
                      )}
                    >
                      <Power size={14} className={isToggling ? "animate-spin" : ""} />
                      {isToggling ? t("starting") : t("start")}
                    </button>
                  </div>
                </div>
                {isOpen && (
                  <div className="border-t border-border p-4 space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {configs.map(([key, meta]) => (
                        <ConfigField key={key} configKey={key} meta={meta}
                          value={values[key]} onChange={(v) => updateVal(key, v)} />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
