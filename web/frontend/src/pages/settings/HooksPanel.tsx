import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FileJson, Plus, Save } from "lucide-react";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { configMetaApi, hooksApi } from "@/lib/api";
import type { HookEntry } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Badge, Button, LoadingBlock, Switch } from "@/components/ui";
import { toast } from "@/stores/toast-store";
import { ScriptHookEditor, ScriptHookTest } from "../hooks/ScriptHookEditor";

type HooksMap = Record<string, HookEntry[]>;

export function HooksPanel() {
  const { t } = useTranslation("settings");
  const client = useQueryClient();
  const [draft, setDraft] = useState<HooksMap>({});
  const [dirty, setDirty] = useState(false);
  useUnsavedChanges(dirty);
  const query = useQuery({
    queryKey: ["hooks-config"],
    queryFn: () => hooksApi.get().then((r) => r.data),
  });
  useEffect(() => {
    if (query.data && !dirty) setDraft(query.data.hooks);
  }, [query.data, dirty]);
  const toggle = useMutation({
    mutationFn: (value: boolean) => configMetaApi.save("hooks_enabled", value),
    onSuccess: () => client.invalidateQueries({ queryKey: ["hooks-config"] }),
    onError: () => toast.error(t("hooks.toggleSaveFailed")),
  });
  const save = useMutation({
    mutationFn: () => hooksApi.save(draft),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ["hooks-config"] });
      setDirty(false);
      toast.success(t("common:saved"));
    },
    onError: () => toast.error(t("hooks.saveFailed")),
  });
  const example = useMutation({
    mutationFn: () => hooksApi.example(),
    onSuccess: (result) => {
      setDraft(result.data);
      setDirty(true);
    },
    onError: () => toast.error(t("hooks.saveFailed")),
  });
  const edit = (event: string, rows: HookEntry[]) => {
    setDraft((prev) => ({ ...prev, [event]: rows }));
    setDirty(true);
  };
  if (query.isError)
    return <Button onClick={() => query.refetch()}>{t("common:retry")}</Button>;
  if (!query.data) return <LoadingBlock />;
  const data = query.data;
  const invalid = Object.values(draft).some((rows) =>
    rows.some(
      (row) =>
        !row.command.trim() ||
        !Number.isFinite(row.timeout ?? 10) ||
        (row.timeout ?? 10) < 1 ||
        (row.timeout ?? 10) > 60,
    ),
  );
  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-3">
          <Switch
            checked={data.enabled}
            label={t("hooks.globalToggle")}
            disabled={toggle.isPending}
            onChange={(value) => toggle.mutate(value)}
          />
          <span className="text-sm font-medium text-heading">
            {t("hooks.globalToggle")}
          </span>
          <div className="ml-auto flex gap-2">
            <Button
              size="sm"
              variant="ghost"
              loading={example.isPending}
              onClick={() => example.mutate()}
            >
              <FileJson size={14} />
              {t("hooks.loadExample")}
            </Button>
            <Button
              size="sm"
              variant="primary"
              loading={save.isPending}
              disabled={!dirty || invalid}
              onClick={() => save.mutate()}
            >
              <Save size={14} />
              {t("hooks.save")}
            </Button>
          </div>
        </div>
        <p className="mt-3 text-xs leading-6 text-muted">
          {t("hookFlow.scriptHint")}
        </p>
        {invalid && (
          <p className="text-xs text-warn">{t("hookFlow.invalid")}</p>
        )}
      </Card>
      {data.events.map((event) => {
        const rows = draft[event] ?? [];
        const add = () =>
          edit(event, [
            ...rows,
            {
              matcher: "*",
              command: "",
              timeout: 10,
              mode: event === "tool_pre" ? "sync" : "async",
            },
          ]);
        return (
          <Card
            key={event}
            title={t(`hooks.events.${event}`, { defaultValue: event })}
            subtitle={t(`hookFlow.eventsHint.${event}`)}
            actions={
              <div className="flex items-center gap-2">
                <Badge variant="neutral">{rows.length}</Badge>
                <Button size="sm" variant="ghost" onClick={add}>
                  <Plus size={14} />
                  {t("hooks.add")}
                </Button>
              </div>
            }
          >
            <div className="space-y-3">
              {rows.map((row, index) => (
                <ScriptHookEditor
                  key={index}
                  row={row}
                  onChange={(patch) =>
                    edit(
                      event,
                      rows.map((value, i) =>
                        i === index ? { ...value, ...patch } : value,
                      ),
                    )
                  }
                  onRemove={() =>
                    edit(
                      event,
                      rows.filter((_, i) => i !== index),
                    )
                  }
                />
              ))}
              {!rows.length && (
                <p className="py-3 text-xs text-muted">{t("hooks.empty")}</p>
              )}
            </div>
            <ScriptHookTest event={event} dirty={dirty} />
          </Card>
        );
      })}
    </div>
  );
}
