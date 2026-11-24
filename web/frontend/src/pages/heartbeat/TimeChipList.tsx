import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Clock, Plus, X } from "lucide-react";
import { Button, Input } from "@/components/ui";

export function TimeChipList({
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
            aria-label={t("schedule.removeTime", { time })}
            className="grid size-6 place-items-center rounded-full hover:text-danger transition-colors pointer-coarse:size-9"
          >
            <X size={11} />
          </button>
        </span>
      ))}

      {adding ? (
        <div className="flex items-center gap-1.5">
          <Input
            type="time"
            aria-label={t("schedule.times")}
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
            aria-label={t("common:cancel")}
            className="grid size-8 place-items-center rounded-md text-muted hover:text-foreground pointer-coarse:size-11"
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
