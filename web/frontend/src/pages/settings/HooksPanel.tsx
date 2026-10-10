import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Activity, Clock, FileJson, Play, Plus, Power, Save, ShieldAlert, ShieldCheck, Trash2, Webhook, Zap,
} from "lucide-react";
import { configMetaApi, hooksApi } from "@/lib/api";
import type { HookEntry, HookRunDetail } from "@/lib/types";
import { Card } from "@/components/common/Card";
import {
  Badge, Button, EmptyState, Input, LoadingBlock, Switch, Textarea,
} from "@/components/ui";
import { cn } from "@/lib/utils";
import { toast } from "@/stores/toast-store";

type HooksMap = Record<string, HookEntry[]>;

/** hooks 管理面板：config/hooks.json 的可视化编辑（总开关/行级停用/测试运行/执行观测）。 */
export function HooksPanel() {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<HooksMap>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["hooks-config"],
    queryFn: () => hooksApi.get().then((r) => r.data),
    refetchInterval: 8000,
  });

  useEffect(() => {
    if (data && !dirty) setDraft(data.hooks ?? {});
  }, [data, dirty]);

  const toggleEnabled = useMutation({
    mutationFn: (enabled: boolean) => configMetaApi.save("hooks_enabled", enabled),
    onSuccess: () => {
      toast.success(t("hooks.toggleSaved"));
      queryClient.invalidateQueries({ queryKey: ["hooks-config"] });
    },
    onError: () => toast.error(t("hooks.toggleSaveFailed")),
  });

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
      // 空行（无 command）在提交前剔除，空事件组省略；
      // enabled 只在显式停用时写盘（保持 hooks.json 与旧版兼容）
      const cleaned: HooksMap = {};
      for (const [event, rows] of Object.entries(draft)) {
        const kept = rows
          .filter((r) => r.command.trim())
          .map((r) => (r.enabled === false ? r : { ...r, enabled: undefined }));
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
    <div className="space-y-4">
      {/* ── 顶部状态栏：总开关 + 关键指标 ── */}
      <Card className="p-4">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
          <div className="flex items-center gap-2.5">
            <Switch
              checked={data.enabled}
              label={t("hooks.globalToggle")}
              disabled={toggleEnabled.isPending}
              onChange={(checked) => toggleEnabled.mutate(checked)}
            />
            <div>
              <p className="text-sm font-medium text-heading leading-none">{t("hooks.globalToggle")}</p>
              <p className="mt-1 text-[11px] text-muted leading-none">
                {data.enabled ? t("hooks.enabledLabel") : t("hooks.disabledLabel")}
              </p>
            </div>
          </div>

          <div className="hidden sm:block h-8 w-px bg-border" />

          <div className="flex items-center gap-4">
            <StatChip icon={Zap} label={t("hooks.statsActive")} value={Object.values(data.active).reduce((a, b) => a + b, 0)} />
            <StatChip icon={ShieldCheck} label={t("hooks.statsExecuted")} value={Object.values(data.stats?.events ?? {}).reduce((a, s) => a + (s?.executed ?? 0), 0)} tone="ok" />
            <StatChip icon={ShieldAlert} label={t("hooks.statsBlocked")} value={Object.values(data.stats?.events ?? {}).reduce((a, s) => a + (s?.blocked ?? 0), 0)} tone="danger" />
          </div>

          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={onLoadExample}>
              <FileJson size={14} /> {t("hooks.loadExample")}
            </Button>
            <Button size="sm" variant="primary" loading={saving} disabled={!dirty} onClick={onSave}>
              <Save size={14} /> {t("hooks.save")}
            </Button>
          </div>
        </div>
        {!data.enabled && (
          <div className="mt-3 flex items-center gap-2 rounded-lg border border-warn/30 bg-warn-subtle px-3 py-2 text-xs text-warn">
            <Power size={13} />
            {t("hooks.disabledBanner")}
          </div>
        )}
      </Card>

      {/* ── 事件分组：紧凑行式布局 ── */}
      {data.events.map((event) => {
        const integration = data.integrations.find((i) => i.event === event);
        const stats = data.stats?.events?.[event];
        const rows = draft[event] ?? [];
        return (
          <Card key={event} className="p-4">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent-subtle text-accent">
                <Webhook size={13} />
              </span>
              <span className="text-sm font-semibold text-heading">
                {t(`hooks.events.${event}`, { defaultValue: event })}
              </span>
              <span className="font-mono text-[10px] text-muted">{event}</span>
              {(data.active[event] ?? 0) > 0 ? (
                <Badge variant="ok">{t("hooks.activeCount", { count: data.active[event] })}</Badge>
              ) : (
                <Badge variant="neutral">{t("hooks.noActive")}</Badge>
              )}
              {stats && stats.blocked > 0 && (
                <Badge variant="danger">{t("hooks.statsBlocked", { count: stats.blocked })}</Badge>
              )}
              {stats && stats.failed > 0 && (
                <Badge variant="warn">{t("hooks.statsFailed", { count: stats.failed })}</Badge>
              )}
              <Button size="sm" variant="ghost" className="ml-auto h-7 px-2 text-xs" onClick={() => addRow(event)}>
                <Plus size={12} /> {t("hooks.add")}
              </Button>
            </div>

            {integration && (
              <p className="mb-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-muted">
                <span className="mt-0.5 shrink-0 text-muted/70">{t("hooks.integrationAt")}</span>
                <span className="font-mono text-[10px] text-muted/90">{integration.where}</span>
                <span className="text-border-strong">·</span>
                <span>{integration.note}</span>
              </p>
            )}

            {rows.length === 0 ? (
              <div className="rounded-lg border border-dashed border-border px-4 py-6 text-center">
                <p className="text-xs text-muted">{t("hooks.empty")}</p>
                <Button size="sm" variant="ghost" className="mt-2 h-7 text-xs" onClick={() => addRow(event)}>
                  <Plus size={12} /> {t("hooks.add")}
                </Button>
              </div>
            ) : (
              <div className="space-y-2">
                {rows.map((row, i) => (
                  <HookRow
                    key={i}
                    row={row}
                    stats={stats}
                    onChange={(patch) => update(event, i, patch)}
                    onRemove={() => removeRow(event, i)}
                    t={t}
                  />
                ))}
              </div>
            )}

            <EventTest event={event} enabled={data.enabled} />
          </Card>
        );
      })}

      {data.events.every((e) => !(draft[e] ?? []).length) && (
        <Card className="p-8">
          <EmptyState icon={Webhook} title={t("hooks.noHooks")} />
        </Card>
      )}

      <p className="text-[11px] leading-relaxed text-muted">{t("hooks.hint")}</p>

      <RecentRuns recent={data.stats?.recent ?? []} />
    </div>
  );
}

/** 顶部指标胶囊 */
function StatChip({ icon: Icon, label, value, tone = "neutral" }: {
  icon: typeof Zap; label: string; value: number; tone?: "ok" | "danger" | "neutral";
}) {
  const color = tone === "ok" ? "text-ok" : tone === "danger" ? "text-danger" : "text-muted";
  return (
    <div className="flex items-center gap-2">
      <Icon size={14} className={color} />
      <div>
        <p className="text-[11px] text-muted leading-none">{label}</p>
        <p className={cn("mt-0.5 text-sm font-semibold leading-none", tone === "neutral" ? "text-heading" : color)}>
          {value}
        </p>
      </div>
    </div>
  );
}

/** 单行钩子：开关 + 匹配器 + 超时 + 命令 + 删除，紧凑高密度。 */
function HookRow({ row, stats, onChange, onRemove, t }: {
  row: HookEntry;
  stats?: { executed: number; blocked: number; failed: number; last_run_at: number | null };
  onChange: (patch: Partial<HookEntry>) => void;
  onRemove: () => void;
  t: (k: string, o?: Record<string, unknown>) => string;
}) {
  const disabled = row.enabled === false;
  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-elevated/60 px-3 py-2.5 transition-colors",
        disabled && "opacity-55",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Switch
          checked={!disabled}
          label={t("hooks.rowToggle")}
          onChange={(checked) => onChange({ enabled: checked })}
        />
        <Input
          value={row.matcher}
          onChange={(e) => onChange({ matcher: e.target.value })}
          placeholder={t("hooks.matcherPlaceholder")}
          aria-label={t("hooks.matcher")}
          className="h-7 w-36 text-xs"
        />
        <Input
          value={String(row.timeout ?? 10)}
          onChange={(e) => onChange({ timeout: Number(e.target.value) || 10 })}
          placeholder={t("hooks.timeout")}
          aria-label={t("hooks.timeout")}
          className="h-7 w-16 text-xs"
        />
        {disabled && <Badge variant="neutral">{t("hooks.rowDisabled")}</Badge>}
        {stats && stats.last_run_at && (
          <span className="ml-auto flex items-center gap-1 font-mono text-[10px] text-muted">
            <Clock size={10} />
            {new Date(stats.last_run_at * 1000).toLocaleTimeString()}
          </span>
        )}
        <Button size="sm" variant="ghost" className="ml-auto h-7 w-7 p-0" onClick={onRemove}>
          <Trash2 size={12} />
        </Button>
      </div>
      <Textarea
        value={row.command}
        onChange={(e) => onChange({ command: e.target.value })}
        placeholder={t("hooks.commandPlaceholder")}
        aria-label={t("hooks.command")}
        rows={2}
        className="mt-2 font-mono text-xs"
      />
    </div>
  );
}

/** 事件级测试：轻量内联，运行后只显一行结果摘要。 */
function EventTest({ event, enabled }: { event: string; enabled: boolean }) {
  const { t } = useTranslation("settings");
  const queryClient = useQueryClient();
  const [toolName, setToolName] = useState("*");
  const [open, setOpen] = useState(false);

  const test = useMutation({
    mutationFn: () => hooksApi.test(event, toolName.trim() || "*"),
    onSuccess: async (res) => {
      const { data: result } = res;
      if (result.executed === 0) toast.info(t("hooks.testNoMatch"));
      else if (result.allowed) toast.success(t("hooks.testOk", { count: result.executed }));
      else toast.error(t("hooks.testBlocked", { reason: result.reason }));
      await queryClient.invalidateQueries({ queryKey: ["hooks-config"] });
    },
    onError: (exc) => {
      const detail = (exc as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail || t("hooks.testFailed"));
    },
  });

  const result = test.data?.data;

  return (
    <div className="mt-3">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1 text-[11px] text-muted hover:text-foreground transition-colors"
        >
          <Play size={11} />
          {t("hooks.testLabel")}
        </button>
        {open && event !== "reply_end" && (
          <Input
            value={toolName}
            onChange={(e) => setToolName(e.target.value)}
            placeholder={t("hooks.testToolPlaceholder")}
            aria-label={t("hooks.testToolLabel")}
            className="h-6 w-44 text-[11px]"
          />
        )}
        {open && (
          <Button size="sm" variant="secondary" className="h-6 px-2 text-[11px]" loading={test.isPending} onClick={() => test.mutate()}>
            {t("hooks.test")}
          </Button>
        )}
      </div>

      {open && result && (
        <div className="mt-2 space-y-1">
          {!enabled && (
            <p className="text-[11px] text-warn">{t("hooks.testDisabledWarning")}</p>
          )}
          {result.results.length === 0 ? (
            <p className="text-[10px] text-muted">{t("hooks.testNoMatch")}</p>
          ) : (
            result.results.map((r, i) => (
              <p key={i} className="flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-muted">
                <Badge variant={r.blocked ? "danger" : r.ok ? "ok" : "warn"}>
                  {r.blocked ? t("hooks.blockedBadge") : r.ok ? "exit 0" : `exit ${r.returncode ?? "?"}`}
                </Badge>
                <span className="max-w-[280px] truncate">{r.command}</span>
                <span>{r.duration_ms}ms</span>
                {r.detail && <span className="max-w-[240px] truncate text-danger/80">{r.detail}</span>}
              </p>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** 最近执行观测：紧凑表格行。 */
function RecentRuns({ recent }: { recent: HookRunDetail[] }) {
  const { t } = useTranslation("settings");
  return (
    <Card className="p-4" title={t("hooks.recentTitle")} subtitle={t("hooks.recentSubtitle")}>
      {recent.length === 0 ? (
        <EmptyState icon={Activity} title={t("hooks.recentEmpty")} />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-border text-muted">
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentTime")}</th>
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentEvent")}</th>
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentMatcher")}</th>
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentCommand")}</th>
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentStatus")}</th>
                <th className="pb-2 pr-3 font-medium">{t("hooks.recentDuration")}</th>
              </tr>
            </thead>
            <tbody>
              {recent.slice(0, 8).map((r, i) => (
                <tr key={i} className="border-b border-border/50 last:border-0 hover:bg-elevated/40 transition-colors">
                  <td className="py-2 pr-3 font-mono text-muted">
                    {r.at ? new Date(r.at * 1000).toLocaleTimeString() : "-"}
                  </td>
                  <td className="py-2 pr-3 font-mono text-accent">{r.event}</td>
                  <td className="py-2 pr-3 font-mono text-muted">{r.matcher}</td>
                  <td className="py-2 pr-3 font-mono text-foreground/80 max-w-[200px] truncate">{r.command}</td>
                  <td className="py-2 pr-3">
                    <Badge variant={r.blocked ? "danger" : r.ok ? "ok" : "warn"}>
                      {r.blocked ? t("hooks.blockedBadge") : r.ok ? "exit 0" : `exit ${r.returncode ?? "?"}`}
                    </Badge>
                  </td>
                  <td className="py-2 pr-3 font-mono text-muted">{r.duration_ms}ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
