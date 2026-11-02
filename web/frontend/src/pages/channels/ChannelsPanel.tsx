import { useSearchParams } from "react-router-dom";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adaptersApi, apiErrorMessage, configMetaApi } from "@/lib/api";
import type { ConfigMetaGroup, ConfigMetaItem, ConfigValues } from "@/lib/types";
import { Save, CheckCircle, RefreshCw, RotateCcw } from "lucide-react";
import { CHANNEL_SETTINGS_COMPONENTS, isChannelHidden, type ChannelSettingsProps } from "@/lib/channel-plugins";
import { ConfigField } from "./ConfigField";
import { AdapterCard } from "./AdapterCard";
import { UnmatchedGroupCard } from "./UnmatchedGroupCard";
import { useDraft } from "@/hooks/useDraft";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AsyncState } from "@/components/common/AsyncState";
import { ListToolbar } from "@/components/common/ListToolbar";
import { Button } from "@/components/ui";
import { useChannelReload } from "./useChannelReload";

export function ChannelsPanel({ onOpenTools }: {
  onOpenTools?: (channel: { key: string; name: string }) => void;
}) {
  const { t } = useTranslation("channels");
  const client = useQueryClient();
  const [params] = useSearchParams();
  const [expanded, setExpanded] = useState<string | null>(params.get("channel"));
  const [search, setSearch] = useState(params.get("channel") ?? "");
  const [saved, showSaved, resetSaved] = useCopyFeedback(2000);
  const adapters = useQuery({
    queryKey: ["adapters"], queryFn: () => adaptersApi.list().then((r) => r.data),
    refetchInterval: 5000, throwOnError: false,
  });
  const metadata = useQuery({
    queryKey: ["configMeta"], queryFn: () => configMetaApi.list().then((r) => r.data), throwOnError: false,
  });
  const configs: Record<string, ConfigMetaItem[]> = {};
  const source: ConfigValues = {};
  for (const group of metadata.data?.groups ?? []) {
    if (!group.group.startsWith("adapter/")) continue;
    configs[group.group.slice(8)] = group.items;
    for (const item of group.items) source[item.key] = item.value !== undefined ? item.value : item.default;
  }
  const draft = useDraft(source);
  useUnsavedChanges(draft.dirty);
  const toggle = useMutation({
    mutationFn: adaptersApi.toggle,
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: ["adapters"] }),
      client.invalidateQueries({ queryKey: ["configMeta"] }),
    ]),
  });
  const save = useMutation({
    mutationFn: async (patch: ConfigValues) => {
      const writes = Object.entries(configs).flatMap(([channel, items]) => {
        const entries = items.filter(item => item.key in patch).map(item => [item.key, patch[item.key]] as const);
        if (!entries.length) return [];
        if (CHANNEL_SETTINGS_COMPONENTS[channel]) {
          const submitted = Object.fromEntries(entries);
          return [configMetaApi.saveChannel(channel, submitted).then(({ data }) => ({ submitted, values: data.values }))];
        }
        return entries.map(([key, value]) => configMetaApi.save(key, value).then(({ data }) => ({
          submitted: { [key]: value }, values: data.value === undefined ? {} : { [key]: data.value },
        })));
      });
      const results = await Promise.allSettled(writes);
      const accepted: ConfigValues = {};
      const persisted: ConfigValues = {};
      let error = "";
      for (const result of results) {
        if (result.status === "rejected") error ||= apiErrorMessage(result.reason, t("saveIncomplete"));
        else {
          Object.assign(accepted, result.value.submitted);
          Object.assign(persisted, result.value.values);
        }
      }
      return { accepted, persisted, error };
    },
    onSuccess: ({ accepted, persisted, error }) => {
      client.setQueryData<{ groups: ConfigMetaGroup[] }>(["configMeta"], (current) => current && ({
        groups: current.groups.map((group) => ({
          ...group, items: group.items.map((item) => Object.prototype.hasOwnProperty.call(persisted, item.key) ? { ...item, value: persisted[item.key] } : item),
        })),
      }));
      draft.acknowledge(accepted);
      void client.invalidateQueries({ queryKey: ["configMeta"] });
      if (!error) showSaved();
    },
  });
  const reload = useChannelReload();
  const apply = useMutation({
    mutationFn: async ({ key, connect }: { key: string; connect: boolean }) => {
      const patch = Object.fromEntries((configs[key] ?? []).filter((item) => item.key in draft.patch)
        .map((item) => [item.key, draft.patch[item.key]]));
      if (Object.keys(patch).length) {
        const { error } = await save.mutateAsync(patch);
        if (error) throw new Error(error);
      }
      if (connect) await adaptersApi.start(key);
    },
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: ["adapters"] }),
      client.invalidateQueries({ queryKey: ["configMeta"] }),
    ]),
  });
  const busy = toggle.isPending || reload.isPending || save.isPending || apply.isPending;
  const update = (key: string, value: unknown) => { draft.update(key, value); resetSaved(); };
  const settingsFor = (key: string, status: string): ChannelSettingsProps => {
    const items = configs[key] ?? [];
    return {
      items, status, busy, dirty: items.some((item) => item.key in draft.patch),
      value: (item) => draft.values[item.key], update: (item, value) => update(item.key, value),
      field: (item, label) => <ConfigField key={item.key} meta={label ? { ...item, description: label } : item}
        prefix={`${key}_`} value={draft.values[item.key]} onChange={(value) => update(item.key, value)} />,
      apply: (connect) => apply.mutateAsync({ key, connect }),
    };
  };
  const term = search.trim().toLowerCase();
  const visible = (key: string, name = "") => !isChannelHidden(key) && (!term ||
    [key, name, t(`config:sections.adapter/${key}`, { defaultValue: key })].some(label => label.toLowerCase().includes(term)));
  const registered = adapters.data?.adapters ?? [];
  const matches = registered.filter((adapter) => visible(adapter.key, adapter.name));
  const registeredKeys = new Set(registered.map((adapter) => adapter.key));
  const unmatched = Object.keys(configs).filter((key) => !registeredKeys.has(key) && visible(key));
  const count = matches.length + unmatched.length;
  return (
    <div className="space-y-5">
      <ListToolbar search={search} onSearch={setSearch} count={count}>
        {saved && !draft.dirty && <span role="status" className="flex items-center gap-1 text-xs text-ok"><CheckCircle size={14} />{t("savedOk")}</span>}
        {draft.dirty && <Button size="sm" onClick={draft.reset} disabled={busy}><RotateCcw size={14} />{t("common:reset")}</Button>}
        <Button size="sm" onClick={() => reload.mutate(null)} loading={reload.isPending && reload.variables === null} disabled={draft.dirty || busy}>
          <RefreshCw size={14} />{t("reload")}
        </Button>
        <Button variant="primary" size="sm" onClick={() => save.mutate(draft.patch)} disabled={!draft.dirty || busy} loading={save.isPending}>
          <Save size={14} />{t("saveConfig")}{draft.dirty && ` (${draft.dirtyKeys.length})`}
        </Button>
      </ListToolbar>
      <p className="text-xs leading-relaxed text-muted">{t("reloadHint")}</p>
      <AsyncState pending={adapters.isPending || metadata.isPending}
        error={!adapters.data ? adapters.error : !metadata.data ? metadata.error : undefined}
        retry={() => { void adapters.refetch(); void metadata.refetch(); }}>
        {!adapters.data?.ready ? <p className="text-sm text-muted">{t("runtimeNotReady")}</p> : (
          <div className="grid gap-4">
            {count === 0 && <p className="py-12 text-center text-muted">{t("common:noMatches")}</p>}
            {matches.map((adapter) => (
              <AdapterCard key={adapter.key} adapter={adapter} settings={settingsFor(adapter.key, adapter.status)} isOpen={expanded === adapter.key} configs={configs[adapter.key] ?? []}
                values={draft.values} toggling={toggle.isPending && toggle.variables === adapter.key}
                busy={busy} reloadDisabled={draft.dirty || busy} reloading={reload.isPending && reload.variables === adapter.key}
                onReload={() => reload.mutate(adapter.key)}
                onToggleExpand={() => setExpanded(expanded === adapter.key ? null : adapter.key)}
                onToggle={() => { if (!busy) toggle.mutate(adapter.key); }} onOpenTools={onOpenTools}
                onUpdateVal={update} onResetDefaults={() => { for (const item of configs[adapter.key] ?? []) update(item.key, item.default); }} />
            ))}
            {unmatched.map((key) => (
              <UnmatchedGroupCard key={key} channelKey={key} settings={settingsFor(key, "stopped")} configs={configs[key] ?? []} values={draft.values}
                isOpen={expanded === key} toggling={toggle.isPending && toggle.variables === key}
                busy={busy}
                onToggleExpand={() => setExpanded(expanded === key ? null : key)}
                onStart={() => { if (!busy) toggle.mutate(key); }} onUpdateVal={update} />
            ))}
          </div>
        )}
      </AsyncState>
    </div>
  );
}
