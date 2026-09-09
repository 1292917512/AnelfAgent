import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FileJson, Plus, Save, Trash2, Webhook } from "lucide-react";
import { hooksApi, type HookEntry } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Badge, Button, EmptyState, Input, LoadingBlock, Textarea } from "@/components/ui";
import { toast } from "@/stores/toast-store";

type HooksMap = Record<string, HookEntry[]>;

/** hooks 管理面板：config/hooks.json 的可视化编辑（保存即热生效） */
export function HooksPanel() {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<HooksMap>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["hooks-config"],
    queryFn: () => hooksApi.get().then((r) => r.data),
  });

  useEffect(() => {
    if (data && !dirty) setDraft(data.hooks ?? {});
  }, [data, dirty]);

  if (isLoading || !data) return <LoadingBlock label={t("common:loading")} />;

  const update = (event: string, index: number, patch: Partial<HookEntry>) => {
    setDraft((prev) => {
      const rows = [...(prev[event] ?? [])];
      rows[index] = { matcher: "*", command: "", ...rows[index], ...patch };
      return { ...prev, [event]: rows };
    });
    setDirty(true);
  };

  const addRow = (event: string) => {
    setDraft((prev) => ({
      ...prev,
      [event]: [...(prev[event] ?? []), { matcher: "*", command: "", timeout: 10 }],
    }));
    setDirty(true);
  };

  const removeRow = (event: string, index: number) => {
    setDraft((prev) => {
      const rows = (prev[event] ?? []).filter((_, i) => i !== index);
      return { ...prev, [event]: rows };
    });
    setDirty(true);
  };

  const onSave = async () => {
    setSaving(true);
    try {
      // 空行（无 command）在提交前剔除，空事件组省略
      const cleaned: HooksMap = {};
      for (const [event, rows] of Object.entries(draft)) {
        const kept = rows.filter((r) => r.command.trim());
        if (kept.length) cleaned[event] = kept;
      }
      const { data: result } = await hooksApi.save(cleaned);
      toast.success(t("hooks.saved", { count: result.count }));
      setDirty(false);
      await queryClient.invalidateQueries({ queryKey: ["hooks-config"] });
    } catch (exc) {
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("hooks.saveFailed"));
    } finally {
      setSaving(false);
    }
  };

  const onLoadExample = async () => {
    const { data: example } = await hooksApi.example();
    setDraft(example);
    setDirty(true);
  };

  return (
    <Card
      title={t("hooks.title")}
      subtitle={t("hooks.subtitle")}
      actions={
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={onLoadExample}>
            <FileJson size={14} /> {t("hooks.loadExample")}
          </Button>
          <Button size="sm" variant="primary" loading={saving} disabled={!dirty} onClick={onSave}>
            <Save size={14} /> {t("hooks.save")}
          </Button>
        </div>
      }
    >
      {data.events.map((event) => {
        const rows = draft[event] ?? [];
        return (
          <div key={event} className="mb-4 last:mb-0">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-medium text-foreground">
                {t(`hooks.events.${event}`, { defaultValue: event })}
              </span>
              <span className="font-mono text-[10px] text-muted">{event}</span>
              {(data.active[event] ?? 0) > 0 && (
                <Badge variant="ok">{t("hooks.activeCount", { count: data.active[event] })}</Badge>
              )}
              <Button size="sm" variant="ghost" onClick={() => addRow(event)}>
                <Plus size={13} /> {t("hooks.add")}
              </Button>
            </div>
            {rows.length === 0 ? (
              <p className="text-xs text-muted">{t("hooks.empty")}</p>
            ) : (
              <ul className="space-y-2">
                {rows.map((row, i) => (
                  <li key={i} className="rounded-md border border-border bg-elevated p-2 space-y-2">
                    <div className="flex items-center gap-2">
                      <Input
                        value={row.matcher}
                        onChange={(e) => update(event, i, { matcher: e.target.value })}
                        placeholder={t("hooks.matcherPlaceholder")}
                        aria-label={t("hooks.matcher")}
                        className="w-40"
                      />
                      <Input
                        value={String(row.timeout ?? 10)}
                        onChange={(e) => update(event, i, { timeout: Number(e.target.value) || 10 })}
                        placeholder={t("hooks.timeout")}
                        aria-label={t("hooks.timeout")}
                        className="w-24"
                      />
                      <Button size="sm" variant="ghost" onClick={() => removeRow(event, i)}>
                        <Trash2 size={13} />
                      </Button>
                    </div>
                    <Textarea
                      value={row.command}
                      onChange={(e) => update(event, i, { command: e.target.value })}
                      placeholder={t("hooks.commandPlaceholder")}
                      aria-label={t("hooks.command")}
                      rows={2}
                    />
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
      {data.events.every((e) => !(draft[e] ?? []).length) && (
        <EmptyState icon={Webhook} title={t("hooks.noHooks")} />
      )}
      <p className="mt-3 text-[11px] text-muted">{t("hooks.hint")}</p>
    </Card>
  );
}
