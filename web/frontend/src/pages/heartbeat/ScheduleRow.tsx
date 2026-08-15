import { useTranslation } from "react-i18next";
import { RotateCcw, Trash2 } from "lucide-react";
import type { TaskConfig, TaskSchedule } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Input, Select } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ReasoningEffortOptions } from "@/components/common/ReasoningEffortSelect";
import { TimeChipList } from "./TimeChipList";

const MODE_OPTIONS = [
  { value: "heartbeat", labelKey: "schedule.modeHeartbeat" },
  { value: "scheduled", labelKey: "schedule.modeScheduled" },
  { value: "idle", labelKey: "schedule.modeIdle" },
  { value: "manual", labelKey: "schedule.modeManual" },
] as const;

/** 单条任务调度绑定行：模式 + 频率/时间 + 模型 + 推理强度 */
export function ScheduleRow({
  schedule: s,
  task,
  interval,
  onUpdate,
  onRemove,
}: {
  schedule: TaskSchedule;
  task: TaskConfig | undefined;
  interval: number;
  onUpdate: (patch: Partial<TaskSchedule>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation("heartbeat");
  return (
    <div className="border border-border rounded-md p-3 bg-elevated">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn("w-1.5 h-1.5 rounded-full flex-shrink-0", task?.enabled !== false ? "bg-ok" : "bg-muted")} />
          <span className="text-sm font-medium text-heading truncate">
            {task?.display_name || s.task_name}
          </span>
          <span className="text-[11px] text-muted">{s.task_name}</span>
        </div>
        <button onClick={onRemove} className="p-1 text-muted hover:text-danger transition-colors">
          <Trash2 size={14} />
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Select
          className="w-32"
          value={s.mode}
          onChange={(e) => onUpdate({ mode: e.target.value as TaskSchedule["mode"] })}
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
              onChange={(e) => onUpdate({ every_n_beats: parseInt(e.target.value) || 10 })}
            />
            <span className="text-xs text-muted">{t("schedule.beats")}</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-accent-subtle text-accent font-medium">
              ≈ {((s.every_n_beats ?? 10) * interval / 60).toFixed(0)} {t("config.minutes")}
            </span>
            {(s.beat_count ?? 0) > 0 && (
              <div className="flex items-center gap-1">
                <span className="text-[11px] text-muted">{t("schedule.progress")}: {s.beat_count}/{s.every_n_beats ?? 10}</span>
                <button
                  onClick={() => onUpdate({ beat_count: 0 })}
                  className="p-0.5 text-muted hover:text-accent"
                  title={t("schedule.resetCounter")}
                >
                  <RotateCcw size={11} />
                </button>
              </div>
            )}
          </div>
        )}

        {s.mode === "idle" && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-muted">{t("schedule.idleAfter")}</span>
            <Input
              type="number" min={1} className="!w-16"
              value={s.every_n_beats ?? 4}
              onChange={(e) => onUpdate({ every_n_beats: parseInt(e.target.value) || 4 })}
            />
            <span className="text-xs text-muted">{t("schedule.idleBeats")}</span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-accent-subtle text-accent font-medium">
              ≈ {((s.every_n_beats ?? 4) * interval / 60).toFixed(0)} {t("config.minutes")}
            </span>
            <span className="text-[11px] text-muted">{t("schedule.idleProgress")}: {s.beat_count ?? 0}/{s.every_n_beats ?? 4}</span>
            <span className="text-[11px] text-muted hidden lg:inline">{t("schedule.idleHint")}</span>
          </div>
        )}

        {s.mode === "scheduled" && (
          <TimeChipList
            times={s.schedule_times ?? []}
            onChange={(times) => onUpdate({ schedule_times: times })}
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
            onChange={(id) => onUpdate({ model_id: id || undefined })}
            className="w-44"
          />
          <Select
            className="w-28"
            value={s.reasoning_effort ?? ""}
            onChange={(e) => onUpdate({ reasoning_effort: e.target.value as TaskSchedule["reasoning_effort"] })}
          >
            <option value="">{t("schedule.globalEffort")}</option>
            <ReasoningEffortOptions t={t} keyPrefix="schedule." />
          </Select>
        </div>
      </div>
    </div>
  );
}
