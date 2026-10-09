import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { adaptersApi, configMetaApi } from "@/lib/api";
import type { ConfigMetaGroup, ConfigMetaItem, ConfigValues } from "@/lib/types";
import { Save, CheckCircle, RefreshCw, RotateCcw } from "lucide-react";
import { isChannelHidden } from "@/lib/channel-plugins";
import { AdapterCard } from "./AdapterCard";
import { UnmatchedGroupCard } from "./UnmatchedGroupCard";
import { useDraft } from "@/hooks/useDraft";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { AsyncState } from "@/components/common/AsyncState";
import { ListToolbar } from "@/components/common/ListToolbar";
import { Button } from "@/components/ui";

export function ChannelsPanel({ onOpenTools }: {
  onOpenTools?: (channel: { key: string; name: string }) => void;
}) {
  const { t } = useTranslation("channels");
  const client = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [search, setSearch] = useState("");
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
    onSettled: () => client.invalidateQueries({ queryKey: ["adapters"] }),
  });
  const save = useMutation({
    mutationFn: async (patch: ConfigValues) => {
      const results = await Promise.allSettled(Object.entries(patch).map(async ([key, value]) => {
        const response = await configMetaApi.save(key, value);
        return { key, submitted: value, value: response.data.value };
      }));
      const accepted: ConfigValues = {};
      const persisted: ConfigValues = {};
      for (const result of results) {
        if (result.status !== "fulfilled") continue;
        accepted[result.value.key] = result.value.submitted;
        if (result.value.value !== undefined) persisted[result.value.key] = result.value.value;
      }
      return { accepted, persisted, complete: results.every((r) => r.status === "fulfilled") };
    },
    onSuccess: ({ accepted, persisted, complete }) => {
      client.setQueryData<{ groups: ConfigMetaGroup[] }>(["configMeta"], (current) => current && ({
        groups: current.groups.map((group) => ({
          ...group, items: group.items.map((item) => Object.prototype.hasOwnProperty.call(persisted, item.key) ? { ...item, value: persisted[item.key] } : item),
        })),
      }));
      draft.acknowledge(accepted);
      void client.invalidateQueries({ queryKey: ["configMeta"] });
      if (complete) showSaved();
    },
  });
  const reload = useMutation({
    mutationFn: adaptersApi.reload,
    onSuccess: () => Promise.all([
      client.invalidateQueries({ queryKey: ["adapters"] }),
      client.invalidateQueries({ queryKey: ["configMeta"] }),
    ]),
  });
  const update = (key: string, value: unknown) => { draft.update(key, value); resetSaved(); };
  const visible = (key: string, name = "") => !isChannelHidden(key) && (key + " " + name).toLowerCase().includes(search.trim().toLowerCase());
  const registered = adapters.data?.adapters ?? [];
  const matches = registered.filter((adapter) => visible(adapter.key, adapter.name));
  const registeredKeys = new Set(registered.map((adapter) => adapter.key));
  const unmatched = Object.keys(configs).filter((key) => !registeredKeys.has(key) && visible(key));
  const count = matches.length + unmatched.length;
  return (
    <div className="space-y-5">
      <ListToolbar search={search} onSearch={setSearch} count={count}>
        {saved && !draft.dirty && <span role="status" className="flex items-center gap-1 text-xs text-ok"><CheckCircle size={14} />{t("savedOk")}</span>}
        {draft.dirty && <Button size="sm" onClick={draft.reset} disabled={save.isPending}><RotateCcw size={14} />{t("common:reset")}</Button>}
        <Button size="sm" onClick={() => reload.mutate()} loading={reload.isPending} disabled={draft.dirty || toggle.isPending}>
          <RefreshCw size={14} />{t("reload")}
        </Button>
        <Button variant="primary" size="sm" onClick={() => save.mutate(draft.patch)} disabled={!draft.dirty} loading={save.isPending}>
          <Save size={14} />{t("saveConfig")}{draft.dirty && ` (${draft.dirtyKeys.length})`}
        </Button>
      </ListToolbar>
      <AsyncState pending={adapters.isPending || metadata.isPending}
        error={!adapters.data ? adapters.error : !metadata.data ? metadata.error : undefined}
        retry={() => { void adapters.refetch(); void metadata.refetch(); }}>
        {!adapters.data?.ready ? <p className="text-sm text-muted">{t("runtimeNotReady")}</p> : (
          <div className="grid gap-4">
            {count === 0 && <p className="py-12 text-center text-muted">{t("common:noMatches")}</p>}
            {matches.map((adapter) => (
              <AdapterCard key={adapter.key} adapter={adapter} isOpen={expanded === adapter.key} configs={configs[adapter.key] ?? []}
                values={draft.values} toggling={toggle.isPending && toggle.variables === adapter.key}
                onToggleExpand={() => setExpanded(expanded === adapter.key ? null : adapter.key)}
                onToggle={() => { if (!toggle.isPending) toggle.mutate(adapter.key); }} onOpenTools={onOpenTools}
                onUpdateVal={update} onResetDefaults={() => { for (const item of configs[adapter.key] ?? []) update(item.key, item.default); }} />
            ))}
            {unmatched.map((key) => (
              <UnmatchedGroupCard key={key} channelKey={key} configs={configs[key] ?? []} values={draft.values}
                isOpen={expanded === key} toggling={toggle.isPending && toggle.variables === key}
                onToggleExpand={() => setExpanded(expanded === key ? null : key)}
                onStart={() => { if (!toggle.isPending) toggle.mutate(key); }} onUpdateVal={update} />
            ))}
          </div>
        )}
      </AsyncState>
    </div>
  );
}
