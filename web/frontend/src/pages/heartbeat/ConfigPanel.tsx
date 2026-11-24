import { isAxiosError } from "axios";
import { useId, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage, heartbeatApi, tasksApi } from "@/lib/api";
import type { HeartbeatConfig, TaskSchedule } from "@/lib/types";
import { useUnsavedChanges } from "@/components/common/UnsavedChanges";
import { PageSkeleton, QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { Save, Plus } from "lucide-react";
import { Button, Input, Switch } from "@/components/ui";
import { ScheduleRow } from "./ScheduleRow";

function definitionOf(schedule: TaskSchedule): TaskSchedule {
  const definition = { ...schedule };
  delete definition.beat_count;
  return definition;
}

export function ConfigPanel() {
  const { t } = useTranslation("heartbeat");
  const queryClient = useQueryClient();
  const formId = useId();

  const { data: config, refetch, isFetching, error: readError } = useQuery({
    queryKey: ["heartbeat-config"], throwOnError: false,
    queryFn: () => heartbeatApi.getConfig().then((r) => r.data),
  });

  const { data: tasks = [] } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data),
  });

  const [draft, setDraft] = useState<Partial<HeartbeatConfig>>({});
  const [scheduleBase, setScheduleBase] = useState<TaskSchedule[] | null>(null);
  const [resets, setResets] = useState<string[]>([]);
  const form = { ...config, ...draft };
  const dirty = Object.keys(draft).length > 0;
  useUnsavedChanges(dirty);
  const activeSchedules = form.task_schedules ?? [];
  const interval = form.interval_seconds ?? 300;
  const invalidIdle = activeSchedules.filter((schedule) => schedule.mode === "idle").length > 1;

  const saveMut = useMutation({
    mutationFn: heartbeatApi.saveConfig,
    onSuccess: async (response, changes) => {
      if (response.data.config) queryClient.setQueryData(["heartbeat-config"], response.data.config);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["heartbeat-config"] }),
        queryClient.invalidateQueries({ queryKey: ["heartbeat-status"] }),
        ...(changes.interval_seconds !== undefined ? [queryClient.invalidateQueries({ queryKey: ["configMeta"] })] : []),
      ]);
      setScheduleBase(null);
      setResets([]);
      setDraft({});
    },
  });

  const conflict = isAxiosError(saveMut.error) && saveMut.error.response?.status === 409;
  const reloadSchedules = async () => {
    const latest = await refetch();
    if (!latest.isSuccess) return;
    setDraft((previous) => { const next = { ...previous }; delete next.task_schedules; return next; });
    setScheduleBase(null);
    setResets([]);
    saveMut.reset();
  };

  const setField = <K extends keyof HeartbeatConfig>(key: K, value: HeartbeatConfig[K]) => {
    if (!conflict) saveMut.reset();
    if (key === "task_schedules") setScheduleBase((base) => base ?? config?.task_schedules ?? []);
    setDraft((previous) => {
      const next = { ...previous, [key]: value };
      if (value === config?.[key]) delete next[key];
      return next;
    });
  };

  const updateSchedule = (idx: number, patch: Partial<TaskSchedule>) => {
    const next = activeSchedules.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    setField("task_schedules", next);
  };

  const removeSchedule = (idx: number) => {
    const next = activeSchedules.filter((_, i) => i !== idx);
    setField("task_schedules", next);
  };

  const addSchedule = (taskName: string) => {
    const next: TaskSchedule[] = [...activeSchedules, { task_name: taskName, mode: "manual" }];
    setField("task_schedules", next);
  };

  const unboundTasks = tasks.filter(
    (t) => !activeSchedules.some((s) => s.task_name === t.name),
  );

  if (!config) return readError ? <QueryError error={readError} retry={() => void refetch()} /> : <PageSkeleton />;

  return (
    <fieldset disabled={saveMut.isPending} className="min-w-0 space-y-4">
      {readError && <QueryError compact error={readError} retry={() => void refetch()} />}
      {/* 基础配置 */}
      <Card title={t("config.title")} subtitle={t("config.subtitle")}>
        <form id={formId} className="space-y-4" onSubmit={(event) => {
          event.preventDefault();
          if (dirty && !invalidIdle && !conflict && !saveMut.isPending) saveMut.mutate({
            ...draft,
            ...(scheduleBase && {
              base_task_schedules: scheduleBase.map(definitionOf),
              task_schedules: activeSchedules.map(definitionOf),
              reset_schedule_counters: resets.filter((name) => activeSchedules.some((s) =>
                s.task_name === name && (s.mode === "heartbeat" || s.mode === "idle"))),
            }),
          });
        }}>
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-heading">{t("config.enabled")}</div>
              <div className="text-xs text-muted">{t("config.enabledDesc")}</div>
            </div>
            <Switch label={t("config.enabled")} checked={!!form.enabled} onChange={(v) => setField("enabled", v)} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="flex flex-col gap-1">
              <label htmlFor={formId + "-interval"} className="text-xs text-muted font-medium">{t("config.interval")}</label>
              <div className="flex items-center gap-2">
                <Input
                  id={formId + "-interval"} type="number" min={60} required
                  value={Number.isFinite(interval) ? interval : ""}
                  onChange={(e) => setField("interval_seconds", e.currentTarget.valueAsNumber)}
                />
                <span className="text-xs text-muted whitespace-nowrap">
                  {t("config.intervalUnit")} {Number.isFinite(interval) && `(${Math.round(interval / 60)} ${t("config.minutes")})`}
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor={formId + "-temperature"} className="text-xs text-muted font-medium">{t("config.temperature")}</label>
              <Input
                id={formId + "-temperature"} type="number" min={0} max={1} step="any" required
                value={Number.isFinite(form.analysis_temperature) ? form.analysis_temperature : ""}
                onChange={(e) => setField("analysis_temperature", e.currentTarget.valueAsNumber)}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label htmlFor={formId + "-conversations"} className="text-xs text-muted font-medium">{t("config.minConversations")}</label>
              <Input
                id={formId + "-conversations"} type="number" min={1} required
                value={Number.isFinite(form.min_conversations_for_analysis) ? form.min_conversations_for_analysis : ""}
                onChange={(e) => setField("min_conversations_for_analysis", e.currentTarget.valueAsNumber)}
              />
            </div>
          </div>
        </form>
      </Card>

      {/* 任务调度绑定 */}
      <Card title={t("schedule.title")} subtitle={t("schedule.subtitle")}>
        <div className="space-y-2.5">
          {activeSchedules.map((s, idx) => (
            <ScheduleRow
              key={s.task_name}
              schedule={s}
              task={tasks.find((t) => t.name === s.task_name)}
              interval={Number.isFinite(interval) ? interval : config.interval_seconds}
              onReset={() => {
                setResets((previous) => [...new Set([...previous, s.task_name])]);
                updateSchedule(idx, { beat_count: 0 });
              }}
              onUpdate={(patch) => updateSchedule(idx, patch)}
              onRemove={() => removeSchedule(idx)}
            />
          ))}

          {unboundTasks.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap pt-1">
              <span className="text-xs text-muted">{t("schedule.addTask")}</span>
              {unboundTasks.map((task) => (
                <button
                  key={task.name}
                  onClick={() => addSchedule(task.name)}
                  className="flex items-center gap-1 px-2 py-1 text-xs rounded-md border border-dashed border-border text-muted hover:border-accent hover:text-accent transition-colors"
                >
                  <Plus size={12} /> {task.display_name || task.name}
                </button>
              ))}
            </div>
          )}
        </div>
      </Card>

      {/* 统一保存 */}
      <div className="flex flex-wrap items-center justify-end gap-3">
        {invalidIdle && <p role="alert" className="text-sm text-danger">{t("schedule.idleSingleError")}</p>}
        {saveMut.error && <p role="alert" className="text-sm text-danger">{apiErrorMessage(saveMut.error, t("common:requestFailed"))}</p>}
        {conflict && <Button onClick={() => void reloadSchedules()} loading={isFetching}>{t("schedule.reloadConflicts")}</Button>}
        {conflict && <span className="text-xs text-muted">{t("schedule.conflictHint")}</span>}
        {saveMut.isSuccess && <p role="status" className="text-sm text-ok">{t("common:saved")}</p>}
        <Button variant="primary" type="submit" form={formId} disabled={!dirty || invalidIdle || conflict} loading={saveMut.isPending}>
          <Save size={14} /> {saveMut.isPending ? t("config.saving") : t("config.save")}
        </Button>
      </div>
    </fieldset>
  );
}
