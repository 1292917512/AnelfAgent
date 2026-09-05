import { useState, useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adaptersApi, configMetaApi, warnApiError } from "@/lib/api";
import type { AdapterInfo, ConfigMetaItem, ConfigValues } from "@/lib/types";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { Save, CheckCircle } from "lucide-react";
import { isChannelHidden } from "@/lib/channel-plugins";
import { AdapterCard } from "@/pages/channels/AdapterCard";
import { UnmatchedGroupCard } from "@/pages/channels/UnmatchedGroupCard";


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

  // 频道配置走统一配置中心数据流（组 adapter/<id>），与 /config 页同源
  const { data: configMeta } = useQuery({
    queryKey: ["configMeta"],
    queryFn: () => configMetaApi.list().then((r) => r.data),
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
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [saveOk, triggerSaveOk, resetSaveOk] = useCopyFeedback(2000);

  // 频道配置组：channelKey -> 该频道的配置项列表
  const configsByChannel: Record<string, ConfigMetaItem[]> = {};
  if (configMeta) {
    for (const group of configMeta.groups) {
      if (!group.group.startsWith("adapter/")) continue;
      configsByChannel[group.group.slice("adapter/".length)] = group.items;
    }
  }

  useEffect(() => {
    if (!configMeta) return;
    const initial: ConfigValues = {};
    for (const items of Object.values(configsByChannel)) {
      for (const item of items) {
        initial[item.key] = item.value !== undefined ? item.value : item.default;
      }
    }
    setValues(initial);
    setDirtyKeys(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [configMeta]);

  const saveMutation = useMutation({
    mutationFn: (keys: string[]) =>
      Promise.all(keys.map((k) => configMetaApi.save(k, values[k]))),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["configMeta"] });
      setDirtyKeys(new Set());
      triggerSaveOk();
    },
  });

  const adapters: AdapterInfo[] = (data?.adapters ?? []).filter(
    (a: AdapterInfo) => !isChannelHidden(a.key),
  );
  const ready = data?.ready ?? false;

  const getConfigsForChannel = (channelKey: string): ConfigMetaItem[] =>
    configsByChannel[channelKey] ?? [];

  const updateVal = (key: string, val: unknown) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirtyKeys((prev) => new Set(prev).add(key));
    resetSaveOk();
  };

  const resetDefaults = (channelKey: string) => {
    const items = configsByChannel[channelKey] ?? [];
    const defaults: ConfigValues = {};
    for (const item of items) defaults[item.key] = item.default;
    setValues((prev) => ({ ...prev, ...defaults }));
    setDirtyKeys((prev) => new Set([...prev, ...items.map((i) => i.key)]));
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

  const adapterKeys = new Set(adapters.map((a) => a.key));
  const unmatchedChannels = Object.keys(configsByChannel).filter(
    (channelKey) => !adapterKeys.has(channelKey) && !isChannelHidden(channelKey),
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-end">
        {dirtyKeys.size > 0 && (
          <div className="flex items-center gap-2">
            {saveOk && (
              <span className="flex items-center gap-1 text-xs text-ok">
                <CheckCircle size={14} /> {t("savedOk")}
              </span>
            )}
            <button onClick={() => saveMutation.mutate([...dirtyKeys])} disabled={saveMutation.isPending}
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

          {/* 已配置但尚未注册启动的频道 */}
          {unmatchedChannels.map((channelKey) => (
            <UnmatchedGroupCard
              key={channelKey}
              channelKey={channelKey}
              configs={configsByChannel[channelKey] ?? []}
              values={values}
              isOpen={expanded === channelKey}
              toggling={togglingKey === channelKey}
              onToggleExpand={() => setExpanded(expanded === channelKey ? null : channelKey)}
              onStart={() => startUnmatched(channelKey)}
              onUpdateVal={updateVal}
            />
          ))}
        </div>
      )}
    </div>
  );
}
