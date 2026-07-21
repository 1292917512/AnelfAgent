import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { configApi, memoryApi } from "@/lib/api";
import type { MemoryFileInfo } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { Calendar, formatDateKey } from "@/components/common/Calendar";
import { Button, ConfirmDialog } from "@/components/ui";
import { cn } from "@/lib/utils";
import { Save, Trash2, FileText } from "lucide-react";

const EVENT_PATH_RE = /memory\/events\/(\d{4}-\d{2}-\d{2})\.md$/;

const eventPath = (date: string) => `memory/events/${date}.md`;

export function DailyNotesPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();

  const { data: files = [] } = useQuery({
    queryKey: ["memoryFiles"],
    queryFn: () => memoryApi.files.list().then((r) => r.data),
  });
  const { data: mindConfig } = useQuery({
    queryKey: ["mindConfig"],
    queryFn: () => configApi.getMind().then((r) => r.data?.config || r.data),
  });
  const retentionDays = Number(
    (mindConfig as Record<string, unknown> | undefined)?.notes_events_retention_days ?? 30,
  );

  /** 日期 → 便签文件 映射 */
  const eventsByDate = useMemo(() => {
    const map = new Map<string, MemoryFileInfo>();
    for (const f of files) {
      const m = EVENT_PATH_RE.exec(f.path);
      const date = m?.[1];
      if (date) map.set(date, f);
    }
    return map;
  }, [files]);
  const sortedDates = useMemo(
    () => [...eventsByDate.keys()].sort().reverse(),
    [eventsByDate],
  );

  const [selected, setSelected] = useState(() => formatDateKey(new Date()));
  const [content, setContent] = useState("");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const selectedFile = eventsByDate.get(selected);

  const selectDate = async (date: string) => {
    setSelected(date);
    const file = eventsByDate.get(date);
    if (file) {
      const r = await memoryApi.files.read(file.path);
      setContent(r.data.content ?? "");
    } else {
      setContent("");
    }
  };

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["memoryFiles"] });
  };

  const saveMutation = useMutation({
    mutationFn: () => memoryApi.files.write(eventPath(selected), content),
    onSuccess: invalidate,
  });
  const deleteMutation = useMutation({
    mutationFn: (path: string) => memoryApi.files.delete(path),
    onSuccess: (_r, path) => {
      invalidate();
      setPendingDelete(null);
      if (path === eventPath(selected)) setContent("");
    },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card title={t("daily.calendarTitle")}>
        <Calendar
          selected={selected}
          onSelect={selectDate}
          markedDates={new Set(eventsByDate.keys())}
        />
        <div className="mt-4 pt-3 border-t border-border space-y-1 max-h-64 overflow-y-auto">
          {sortedDates.length === 0 && (
            <p className="text-xs text-muted py-2 text-center">{t("daily.empty")}</p>
          )}
          {sortedDates.map((date) => {
            const f = eventsByDate.get(date)!;
            return (
              <div
                key={date}
                className={cn(
                  "group flex items-center gap-2 p-2 rounded-md text-sm transition-colors cursor-pointer",
                  selected === date ? "bg-accent-subtle text-accent" : "text-foreground hover:bg-hover",
                )}
                onClick={() => selectDate(date)}
              >
                <FileText size={14} className="shrink-0 text-muted" />
                <span className="font-mono">{date}</span>
                <span className="text-[11px] text-muted ml-auto">
                  {t("nLines", { count: Number(f.lines) })}
                </span>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setPendingDelete(f.path); }}
                  className="opacity-0 group-hover:opacity-100 p-1 rounded text-muted hover:text-danger transition-all"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            );
          })}
        </div>
      </Card>

      <Card
        title={selected}
        subtitle={selectedFile ? undefined : t("daily.newNoteHint")}
        className="md:col-span-2"
        actions={
          <Button variant="primary" size="sm" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
            <Save size={14} /> {t("common:save")}
          </Button>
        }
      >
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={16}
          placeholder={t("daily.placeholder")}
          className="w-full bg-elevated border border-input rounded-md px-3 py-2 text-sm text-foreground font-mono outline-none focus:border-ring resize-y"
        />
        <p className="mt-2 text-[11px] text-muted">
          {t("daily.retentionHint", { days: retentionDays })}
        </p>
      </Card>

      <ConfirmDialog
        open={pendingDelete !== null}
        onClose={() => setPendingDelete(null)}
        onConfirm={() => pendingDelete && deleteMutation.mutate(pendingDelete)}
        title={t("daily.deleteTitle")}
        message={t("daily.deleteConfirm", { date: pendingDelete?.match(EVENT_PATH_RE)?.[1] ?? "" })}
        confirmText={t("common:delete")}
        cancelText={t("common:cancel")}
        danger
        loading={deleteMutation.isPending}
      />
    </div>
  );
}
