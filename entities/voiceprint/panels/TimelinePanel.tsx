import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { voiceprintApi } from "./api";
import type { VoiceSegment } from "./types";
import { Badge, EmptyState, Select, Spinner } from "@/components/ui";
import { formatOffset } from "./format";

type RangeKey = "today" | "yesterday" | "d3" | "d7" | "all";

/** 说话人分色调色板（按 speaker_id 确定性取色，同人同色） */
const PALETTE = [
  "#3b82f6", "#22c55e", "#f59e0b", "#ef4444", "#8b5cf6",
  "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#14b8a6",
];

function speakerColor(speakerId: number | null): string {
  if (speakerId === null) return "#6b7280";
  return PALETTE[Math.abs(speakerId) % PALETTE.length] ?? "#6b7280";
}

function dayStart(offsetDays: number): number {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return Math.floor(d.getTime() / 1000) - offsetDays * 86400;
}

function fmtDay(ns: number): string {
  const d = new Date(ns / 1_000_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtTime(ns: number): string {
  const d = new Date(ns / 1_000_000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** 时间线：指定时间段内谁在什么时间说了什么（说话人分色 + 按录制分块）。 */
export function TimelinePanel() {
  const { t } = useTranslation("voiceprint");
  const [range, setRange] = useState<RangeKey>("today");
  const [speakerId, setSpeakerId] = useState("");
  // 挂载时刻快照（渲染期不取实时时钟，保证渲染幂等）
  const [nowSnapshot] = useState(() => Math.floor(Date.now() / 1000));

  const { data: speakersData } = useQuery({
    queryKey: ["voiceprintSpeakers", "all"],
    queryFn: () => voiceprintApi.speakers({ limit: 200 }).then((r) => r.data),
  });

  const rangeParams = useMemo(() => {
    switch (range) {
      case "today": return { time_from: String(dayStart(0)) };
      case "yesterday": return { time_from: String(dayStart(1)), time_to: String(dayStart(0) - 1) };
      case "d3": return { time_from: String(dayStart(3)) };
      case "d7": return { time_from: String(dayStart(7)) };
      default: return { time_from: String(nowSnapshot - 30 * 86400) };
    }
  }, [range, nowSnapshot]);

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintTimeline", range, speakerId, rangeParams],
    queryFn: () =>
      voiceprintApi.segments({
        ...rangeParams,
        speaker_id: speakerId ? Number(speakerId) : undefined,
        order: "asc",
        limit: 200,
      }).then((r) => r.data),
    refetchInterval: 30_000,
  });

  const speakers = speakersData?.items ?? [];
  const items = useMemo(() => data?.items ?? [], [data]);

  // 按「日 → 录制块」分组（时间正序）
  const blocks = useMemo(() => {
    const result: Array<
      | { type: "day"; day: string }
      | { type: "recording"; path: string; segments: VoiceSegment[] }
    > = [];
    let lastDay = "";
    for (const seg of items) {
      const day = fmtDay(seg.ts_ns);
      if (day !== lastDay) {
        result.push({ type: "day", day });
        lastDay = day;
      }
      const last = result[result.length - 1];
      if (last && last.type === "recording" && last.path === seg.recording_path) {
        last.segments.push(seg);
      } else {
        result.push({ type: "recording", path: seg.recording_path, segments: [seg] });
      }
    }
    return result;
  }, [items]);

  const RANGES: RangeKey[] = ["today", "yesterday", "d3", "d7", "all"];

  return (
    <div className="space-y-3">
      {/* 单行工具栏：快捷时间段 + 说话人 */}
      <div className="flex items-center gap-1.5 flex-nowrap overflow-x-auto pb-1">
        {RANGES.map((key) => (
          <button
            key={key}
            onClick={() => setRange(key)}
            className={`shrink-0 rounded-full px-3 py-1 text-xs transition-colors ${
              range === key
                ? "bg-accent text-primary-foreground"
                : "border border-border bg-elevated text-muted hover:bg-hover"
            }`}
          >
            {t(`timeline.ranges.${key}`)}
          </button>
        ))}
        <Select
          className="w-36 shrink-0"
          value={speakerId}
          onChange={(e) => setSpeakerId(e.target.value)}
        >
          <option value="">{t("filters.allSpeakers")}</option>
          {speakers.map((s) => (
            <option key={s.id} value={s.id}>{s.name || s.speaker_key}</option>
          ))}
        </Select>
        <div className="flex-1" />
        <span className="text-xs text-muted shrink-0">
          {t("timeline.count", { count: data?.total ?? 0 })}
        </span>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("timeline.empty")} description={t("timeline.emptyHint")} />
      ) : (
        <div className="space-y-3">
          {blocks.map((block, bi) =>
            block.type === "day" ? (
              <div key={`day-${bi}`} className="flex items-center gap-2 pt-1">
                <span className="text-xs font-semibold text-muted">{block.day}</span>
                <div className="flex-1 border-t border-border" />
              </div>
            ) : (
              <div
                key={`rec-${bi}`}
                className="rounded-lg border border-border bg-card px-3 py-2 space-y-1.5"
              >
                {block.segments.map((seg) => {
                  const color = speakerColor(seg.speaker_id);
                  const label = seg.speaker_name || seg.speaker_key || t("fields.unknownSpeaker");
                  return (
                    <div key={seg.id} className="flex items-start gap-2 text-sm">
                      <span className="text-xs text-muted whitespace-nowrap pt-0.5 w-14">
                        {fmtTime(seg.ts_ns)}
                      </span>
                      <button
                        title={t("timeline.filterBySpeaker")}
                        onClick={() =>
                          setSpeakerId(seg.speaker_id ? String(seg.speaker_id) : "")}
                        className="shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium text-white"
                        style={{ backgroundColor: color }}
                      >
                        {label}
                      </button>
                      <div className="min-w-0 flex-1">
                        <p className="text-foreground break-words">
                          {seg.transcript || t("fields.noTranscript")}
                        </p>
                        <div className="flex gap-1 mt-0.5">
                          {seg.is_new_speaker && (
                            <Badge variant="warn">{t("status.newSpeaker")}</Badge>
                          )}
                          {seg.similarity > 0 && (
                            <span className="text-[10px] text-muted">
                              sim {seg.similarity.toFixed(2)}
                            </span>
                          )}
                          <span className="text-[10px] text-muted">
                            [{formatOffset(seg.start_ms)}-{formatOffset(seg.end_ms)}]
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
