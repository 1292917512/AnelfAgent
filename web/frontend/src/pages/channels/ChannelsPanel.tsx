import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adaptersApi, warnApiError } from "@/lib/api";
import type { AdapterInfo, ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { Save, CheckCircle } from "lucide-react";
import { isChannelHidden } from "@/lib/channel-plugins";
import { AdapterCard } from "@/pages/channels/AdapterCard";
import { UnmatchedGroupCard } from "@/pages/channels/UnmatchedGroupCard";
import type { ConfigMeta } from "@/pages/channels/ConfigField";


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

  const [values, setValues] = useState<ConfigValues>({});
  const [dirty, setDirty] = useState(false);
  const [saveOk, triggerSaveOk, resetSaveOk] = useCopyFeedback(2000);

  useEffect(() => {
    if (!rawConfigs) return;
    const initial: ConfigValues = {};
    for (const [key, meta] of Object.entries(rawConfigs)) {
      initial[key] = meta.value !== undefined ? meta.value : meta.default;
    }
    setValues(initial);
    setDirty(false);
  }, [rawConfigs]);

  const saveMutation = useMutation({
    mutationFn: (vals: ConfigValues) => adaptersApi.saveConfigs(vals),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["adapterConfigs"] });
      setDirty(false);
      triggerSaveOk();
    },
  });

  const adapters: AdapterInfo[] = (data?.adapters ?? []).filter(
    (a: AdapterInfo) => !isChannelHidden(a.key),
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
    const groupKey = `adapter/${channelKey}`;
    return configGroups[groupKey] ?? [];
  };

  const updateVal = (key: string, val: unknown) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
    resetSaveOk();
  };

  const resetDefaults = (channelKey: string) => {
    if (!rawConfigs) return;
    const groupKey = `adapter/${channelKey}`;
    const defaults: ConfigValues = {};
    for (const [k, m] of Object.entries(rawConfigs)) {
      if (m.group === groupKey) defaults[k] = m.default;
    }
    setValues((prev) => ({ ...prev, ...defaults }));
    setDirty(true);
    resetSaveOk();
  };

  const startUnmatched = (channelKey: string) => {
    togglingRef.current = { key: channelKey, prevStatus: "stopped" };
    setTogglingKey(channelKey);
    adaptersApi.toggle(channelKey).then(() => {
      queryClient.invalidateQueries({ queryKey: ["adapters"] });
    }).catch((e) => {
      warnApiError(e);
      setTogglingKey(null);
      togglingRef.current = null;
    });
  };

  const allConfigGroups = Object.keys(configGroups);
  const adapterKeys = new Set(adapters.map((a) => a.key));
  const unmatchedGroups = allConfigGroups.filter((g) => {
    const channelKey = g.replace("adapter/", "");
    return !adapterKeys.has(channelKey) && !isChannelHidden(channelKey);
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
          {adapters.map((a) => (
            <AdapterCard
              key={a.key}
              adapter={a}
              isOpen={expanded === a.key}
              configs={getConfigsForChannel(a.key)}
              values={values}
              toggling={togglingKey === a.key}
              onToggleExpand={() => setExpanded(expanded === a.key ? null : a.key)}
              onToggle={() => toggleMutation.mutate(a.key)}
              onOpenTools={onOpenTools}
              onUpdateVal={updateVal}
              onResetDefaults={() => resetDefaults(a.key)}
            />
          ))}

          {/* Unmatched config groups (not yet registered as channels) */}
          {unmatchedGroups.map((group) => {
            const channelKey = group.replace("adapter/", "");
            return (
              <UnmatchedGroupCard
                key={channelKey}
                channelKey={channelKey}
                configs={configGroups[group] ?? []}
                values={values}
                isOpen={expanded === channelKey}
                toggling={togglingKey === channelKey}
                onToggleExpand={() => setExpanded(expanded === channelKey ? null : channelKey)}
                onStart={() => startUnmatched(channelKey)}
                onUpdateVal={updateVal}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
