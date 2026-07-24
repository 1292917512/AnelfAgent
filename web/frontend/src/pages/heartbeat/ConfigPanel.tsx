import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { heartbeatApi, tasksApi, type HeartbeatConfig, type TaskSchedule, type TaskConfig } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Save, Plus, Trash2, RotateCcw, X, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button, Input, Select, Switch } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";

const MODE_OPTIONS = [
  { value: "heartbeat", labelKey: "schedule.modeHeartbeat" },
  { value: "scheduled", labelKey: "schedule.modeScheduled" },
  { value: "manual", labelKey: "schedule.modeManual" },
] as const;

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

  const updateSchedule = (idx: number, patch: Record<string, unknown>) => {
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
          {activeSchedules.map((s, idx) => {
            const task = (tasks as TaskConfig[]).find((t) => t.name === s.task_name);
            return (
              <div key={s.task_name} className="border border-border rounded-md p-3 bg-elevated">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", task?.enabled !== false ? "bg-ok" : "bg-muted")} />
                    <span className="text-sm font-medium text-heading truncate">
                      {task?.display_name || s.task_name}
                    </span>
                    <span className="text-[11px] text-muted">{s.task_name}</span>
                  </div>
                  <button onClick={() => removeSchedule(idx)} className="p-1 text-muted hover:text-danger transition-colors">
                    <Trash2 size={14} />
                  </button>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <Select
                    className="w-32"
                    value={s.mode}
                    onChange={(e) => updateSchedule(idx, { mode: e.target.value })}
                  >
                    {MODE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>{t(o.labelKey)}</option>
                    ))}
                  </Select>

                  {s.mode === "heartbeat" && (
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs text-muted">{t("schedule.every")}</span>
                      <Input
                        type="number" min={1} className="!w-16"
                        value={s.every_n_beats ?? 10}
                        onChange={(e) => updateSchedule(idx, { every_n_beats: parseInt(e.target.value) || 10 })}
                      />
                      <span className="text-xs text-muted">{t("schedule.beats")}</span>
                      <span className="text-xs px-2 py-0.5 rounded-full bg-accent-subtle text-accent font-medium">
                        ≈ {((s.every_n_beats ?? 10) * interval / 60).toFixed(0)} {t("config.minutes")}
                      </span>
                      {(s.beat_count ?? 0) > 0 && (
                        <div className="flex items-center gap-1">
                          <span className="text-[11px] text-muted">{t("schedule.progress")}: {s.beat_count}/{s.every_n_beats ?? 10}</span>
                          <button
                            onClick={() => updateSchedule(idx, { beat_count: 0 })}
                            className="p-0.5 text-muted hover:text-accent"
                            title={t("schedule.resetCounter")}
                          >
                            <RotateCcw size={11} />
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {s.mode === "scheduled" && (
                    <TimeChipList
                      times={s.schedule_times ?? []}
                      onChange={(times) => updateSchedule(idx, { schedule_times: times })}
                    />
                  )}

                  {s.mode === "manual" && (
                    <span className="text-xs text-muted italic">{t("schedule.manualOnly")}</span>
                  )}

                  <div className="flex items-center gap-2 ml-auto flex-wrap">
                    <ModelSelect
                      modelType="chat"
                      allowEmpty
                      value={s.model_id ?? ""}
                      onChange={(id) => updateSchedule(idx, { model_id: id || null })}
                      className="w-44"
                    />
                    <Select
                      className="w-28"
                      value={s.reasoning_effort ?? ""}
                      onChange={(e) => updateSchedule(idx, { reasoning_effort: e.target.value })}
                    >
                      <option value="">{t("schedule.globalEffort")}</option>
                      <option value="off">{t("schedule.effortOff")}</option>
                      <option value="minimal">{t("schedule.effortMinimal")}</option>
                      <option value="low">{t("schedule.effortLow")}</option>
                      <option value="medium">{t("schedule.effortMedium")}</option>
                      <option value="high">{t("schedule.effortHigh")}</option>
                      <option value="xhigh">{t("schedule.effortXhigh")}</option>
                      <option value="max">{t("schedule.effortMax")}</option>
                    </Select>
                  </div>
                </div>
              </div>
            );
          })}

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

function TimeChipList({
  times,
  onChange,
}: {
  times: string[];
  onChange: (times: string[]) => void;
}) {
  const { t } = useTranslation("heartbeat");
  const [adding, setAdding] = useState(false);
  const [newTime, setNewTime] = useState("08:00");

  const addTime = () => {
    if (!newTime) return;
    const normalized = newTime.slice(0, 5);
    if (!times.includes(normalized)) {
      onChange([...times, normalized].sort());
    }
    setAdding(false);
    setNewTime("08:00");
  };

  const removeTime = (idx: number) => {
    onChange(times.filter((_, i) => i !== idx));
  };

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <Clock size={13} className="text-muted flex-shrink-0" />

      {times.map((time, idx) => (
        <span
          key={time}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded-full bg-accent-subtle text-accent border border-accent/20"
        >
          {time}
          <button
            onClick={() => removeTime(idx)}
            className="hover:text-danger transition-colors"
          >
            <X size={11} />
          </button>
        </span>
      ))}

      {adding ? (
        <div className="flex items-center gap-1.5">
          <Input
            type="time"
            className="!w-28 !h-7 !text-xs"
            value={newTime}
            onChange={(e) => setNewTime(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") addTime(); }}
            autoFocus
          />
          <Button variant="primary" size="sm" onClick={addTime}>
            {t("schedule.addTime")}
          </Button>
          <button
            onClick={() => setAdding(false)}
            className="p-1 text-muted hover:text-foreground"
          >
            <X size={13} />
          </button>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full border border-dashed border-border text-muted hover:border-accent hover:text-accent transition-colors"
        >
          <Plus size={11} /> {t("schedule.addTime")}
        </button>
      )}
    </div>
  );
}
