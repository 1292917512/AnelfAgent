import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { heartbeatApi, tasksApi, type HeartbeatConfig, type TaskSchedule, type TaskConfig } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Save, Plus } from "lucide-react";
import { Button, Input, Switch } from "@/components/ui";
import { ScheduleRow } from "./ScheduleRow";

export function ConfigPanel() {
  const { t } = useTranslation("heartbeat");
  const queryClient = useQueryClient();

  const { data: config } = useQuery({
    queryKey: ["heartbeat-config"],
    queryFn: () => heartbeatApi.getConfig().then((r) => r.data),
  });

  const { data: tasks = [] } = useQuery({
    queryKey: ["tasks"],
    queryFn: () => tasksApi.list().then((r) => r.data as TaskConfig[]),
  });

  const [form, setForm] = useState<Partial<HeartbeatConfig>>({});
  const [schedules, setSchedules] = useState<TaskSchedule[] | null>(null);

  useEffect(() => {
    if (config) {
      setForm(config);
      setSchedules(null);
    }
  }, [config]);

  const activeSchedules = schedules ?? form.task_schedules ?? config?.task_schedules ?? [];
  const interval = form.interval_seconds ?? config?.interval_seconds ?? 300;

  const saveMut = useMutation({
    mutationFn: (data: Partial<HeartbeatConfig>) => heartbeatApi.saveConfig(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["heartbeat-config"] });
      queryClient.invalidateQueries({ queryKey: ["heartbeat-status"] });
      setSchedules(null);
    },
  });

  const setField = <K extends keyof HeartbeatConfig>(key: K, value: HeartbeatConfig[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const updateSchedule = (idx: number, patch: Partial<TaskSchedule>) => {
    const next = activeSchedules.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    setSchedules(next);
    setForm((prev) => ({ ...prev, task_schedules: next }));
  };

  const removeSchedule = (idx: number) => {
    const next = activeSchedules.filter((_, i) => i !== idx);
    setSchedules(next);
    setForm((prev) => ({ ...prev, task_schedules: next }));
  };

  const addSchedule = (taskName: string) => {
    const next: TaskSchedule[] = [...activeSchedules, { task_name: taskName, mode: "manual" }];
    setSchedules(next);
    setForm((prev) => ({ ...prev, task_schedules: next }));
  };

  const unboundTasks = (tasks as TaskConfig[]).filter(
    (t) => !activeSchedules.some((s) => s.task_name === t.name),
  );

  if (!config) return null;

  const handleSave = () => {
    const payload: Partial<HeartbeatConfig> = { ...form };
    if (schedules !== null) {
      payload.task_schedules = schedules;
    }
    saveMut.mutate(payload);
  };

  return (
    <div className="space-y-4">
      {/* 基础配置 */}
      <Card title={t("config.title")} subtitle={t("config.subtitle")}>
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-heading">{t("config.enabled")}</div>
              <div className="text-xs text-muted">{t("config.enabledDesc")}</div>
            </div>
            <Switch checked={!!form.enabled} onChange={(v) => setField("enabled", v)} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted font-medium">{t("config.interval")}</label>
              <div className="flex items-center gap-2">
                <Input
                  type="number" min={10} step={10}
                  value={form.interval_seconds ?? 300}
                  onChange={(e) => setField("interval_seconds", parseInt(e.target.value) || 300)}
                />
                <span className="text-xs text-muted whitespace-nowrap">
                  {t("config.intervalUnit")} ({Math.round((form.interval_seconds ?? 300) / 60)} {t("config.minutes")})
                </span>
              </div>
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted font-medium">{t("config.temperature")}</label>
              <Input
                type="number" min={0} max={1} step={0.1}
                value={form.analysis_temperature ?? 0.7}
                onChange={(e) => setField("analysis_temperature", parseFloat(e.target.value) || 0.7)}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-xs text-muted font-medium">{t("config.minConversations")}</label>
              <Input
                type="number" min={1}
                value={form.min_conversations_for_analysis ?? 3}
                onChange={(e) => setField("min_conversations_for_analysis", parseInt(e.target.value) || 3)}
              />
            </div>
          </div>
        </div>
      </Card>

      {/* 任务调度绑定 */}
      <Card title={t("schedule.title")} subtitle={t("schedule.subtitle")}>
        <div className="space-y-2.5">
          {activeSchedules.map((s, idx) => (
            <ScheduleRow
              key={s.task_name}
              schedule={s}
              task={(tasks as TaskConfig[]).find((t) => t.name === s.task_name)}
              interval={interval}
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
      <div className="flex justify-end">
        <Button variant="primary" onClick={handleSave} loading={saveMut.isPending}>
          <Save size={14} /> {saveMut.isPending ? t("config.saving") : t("config.save")}
        </Button>
      </div>
    </div>
  );
}
