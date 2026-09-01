import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { CheckCheck, Pencil, Search, Trash2 } from "lucide-react";
import { voiceprintApi } from "./api";
import type { VoiceSegment } from "./types";
import {
  Badge, Button, EmptyState, Input, Modal, Select, Spinner, Textarea, toast,
} from "@/components/ui";
import { formatNs, formatOffset } from "./format";

/** 话语检索：语义/全文混合搜索 + 说话人/时间过滤 + 归属改派 + 文本修订 + 已读。 */
export function TranscriptsPanel() {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();
  const [q, setQ] = useState("");
  const [submittedQ, setSubmittedQ] = useState("");
  const [speakerId, setSpeakerId] = useState("");
  const [timeFrom, setTimeFrom] = useState("");
  const [timeTo, setTimeTo] = useState("");
  const [editTarget, setEditTarget] = useState<VoiceSegment | null>(null);
  const [editText, setEditText] = useState("");

  const { data: speakersData } = useQuery({
    queryKey: ["voiceprintSpeakers", "all"],
    queryFn: () => voiceprintApi.speakers({ limit: 200 }).then((r) => r.data),
  });

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintSegments", submittedQ, speakerId, timeFrom, timeTo],
    queryFn: () =>
      voiceprintApi.segments({
        q: submittedQ || undefined,
        speaker_id: speakerId ? Number(speakerId) : undefined,
        time_from: timeFrom || undefined,
        time_to: timeTo || undefined,
        limit: 50,
      }).then((r) => r.data),
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ["voiceprintSegments"] });

  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("messages.opFailed"));
  };

  const reassignMutation = useMutation({
    mutationFn: ({ id, target }: { id: number; target: number | null }) =>
      voiceprintApi.updateSegment(id, { speaker_id: target }),
    onSuccess: () => {
      toast.success(t("messages.reassignSuccess"));
      invalidate();
    },
    onError,
  });

  const editMutation = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) =>
      voiceprintApi.updateSegment(id, { transcript: text }),
    onSuccess: () => {
      toast.success(t("messages.updateSuccess"));
      setEditTarget(null);
      invalidate();
    },
    onError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => voiceprintApi.deleteSegment(id),
    onSuccess: () => invalidate(),
    onError,
  });

  const markAllMutation = useMutation({
    mutationFn: () => voiceprintApi.markRead(),
    onSuccess: (r) => {
      toast.success(t("messages.markedRead", { count: r.data.marked_read }));
      invalidate();
    },
    onError,
  });

  const speakers = speakersData?.items ?? [];
  const items = data?.items ?? [];

  const speakerName = (seg: VoiceSegment) =>
    seg.speaker_name || seg.speaker_key || t("fields.unknownSpeaker");

  return (
    <div className="space-y-3">
      {/* 单行紧凑筛选栏（横向溢出可滚动，不换行） */}
      <div className="flex items-center gap-1.5 flex-nowrap overflow-x-auto pb-1">
        <Input
          className="w-48 min-w-40 shrink-0"
          placeholder={t("filters.searchPlaceholder")}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && setSubmittedQ(q)}
        />
        <Button size="sm" variant="secondary" className="shrink-0" onClick={() => setSubmittedQ(q)}>
          <Search size={14} />
        </Button>
        <Select className="w-32 shrink-0" value={speakerId} onChange={(e) => setSpeakerId(e.target.value)}>
          <option value="">{t("filters.allSpeakers")}</option>
          {speakers.map((s) => (
            <option key={s.id} value={s.id}>{s.name || s.speaker_key}</option>
          ))}
        </Select>
        <Input
          type="date" className="w-32 shrink-0" title={t("filters.timeFrom")}
          value={timeFrom} onChange={(e) => setTimeFrom(e.target.value)}
        />
        <span className="text-muted shrink-0">-</span>
        <Input
          type="date" className="w-32 shrink-0" title={t("filters.timeTo")}
          value={timeTo} onChange={(e) => setTimeTo(e.target.value)}
        />
        <div className="flex-1 min-w-2" />
        <Button
          size="sm" variant="ghost" className="shrink-0"
          loading={markAllMutation.isPending}
          onClick={() => markAllMutation.mutate()}
        >
          <CheckCheck size={14} className="mr-1" />
          {t("actions.markAllRead")}
        </Button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("empty.segments")} description={t("empty.segmentsHint")} />
      ) : (
        <div className="space-y-2">
          {items.map((seg) => (
            <div
              key={seg.id}
              className="rounded-lg border border-border bg-card px-3 py-2 text-sm space-y-1"
            >
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span>{formatNs(seg.ts_ns)}</span>
                <span>[{formatOffset(seg.start_ms)}-{formatOffset(seg.end_ms)}]</span>
                <Badge variant={seg.is_new_speaker ? "warn" : "accent"}>
                  {speakerName(seg)}
                </Badge>
                {seg.is_new_speaker && <Badge variant="warn">{t("status.newSpeaker")}</Badge>}
                {!seg.read && <Badge variant="info">{t("status.unread")}</Badge>}
                {seg.similarity > 0 && <span>sim {seg.similarity.toFixed(3)}</span>}
                {seg.score !== undefined && <span>score {seg.score.toFixed(3)}</span>}
                <div className="flex-1" />
                <Select
                  value={seg.speaker_id ?? ""}
                  onChange={(e) =>
                    reassignMutation.mutate({
                      id: seg.id,
                      target: e.target.value ? Number(e.target.value) : null,
                    })
                  }
                >
                  <option value="">{t("fields.unknownSpeaker")}</option>
                  {speakers.map((s) => (
                    <option key={s.id} value={s.id}>{s.name || s.speaker_key}</option>
                  ))}
                </Select>
                <Button
                  size="sm" variant="ghost"
                  onClick={() => { setEditTarget(seg); setEditText(seg.transcript); }}
                >
                  <Pencil size={13} />
                </Button>
                <Button size="sm" variant="ghost" onClick={() => deleteMutation.mutate(seg.id)}>
                  <Trash2 size={13} />
                </Button>
              </div>
              <p className="text-foreground">{seg.transcript || t("fields.noTranscript")}</p>
              {seg.source_file && (
                <p className="text-[10px] text-muted truncate">{seg.source_file}</p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 单条转写修订 */}
      <Modal
        open={editTarget !== null}
        onClose={() => setEditTarget(null)}
        title={t("modals.editTranscriptTitle")}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditTarget(null)}>
              {t("common:cancel")}
            </Button>
            <Button
              loading={editMutation.isPending}
              disabled={!editText.trim() || editText === editTarget?.transcript}
              onClick={() =>
                editTarget && editMutation.mutate({ id: editTarget.id, text: editText.trim() })}
            >
              {t("common:save")}
            </Button>
          </>
        }
      >
        <Textarea
          rows={4}
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
          placeholder={t("modals.editTranscriptPlaceholder")}
        />
        <p className="mt-1 text-xs text-muted">{t("modals.editTranscriptHint")}</p>
      </Modal>
    </div>
  );
}
