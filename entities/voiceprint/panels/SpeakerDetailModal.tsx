import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Trash2 } from "lucide-react";
import { voiceprintApi } from "./api";
import { Badge, Button, Input, Modal, Spinner, Textarea, toast } from "@/components/ui";
import { formatDuration, formatNs, formatOffset } from "./format";

interface SpeakerDetailModalProps {
  speakerId: number;
  onClose: () => void;
}

/** 说话人详情：档案编辑 / 样本池管理 / 近期话语。 */
export function SpeakerDetailModal({ speakerId, onClose }: SpeakerDetailModalProps) {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();
  const [name, setName] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [threshold, setThreshold] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintSpeaker", speakerId],
    queryFn: () => voiceprintApi.speakerDetail(speakerId).then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["voiceprintSpeaker", speakerId] });
    queryClient.invalidateQueries({ queryKey: ["voiceprintSpeakers"] });
  };

  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("messages.opFailed"));
  };

  const updateMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      voiceprintApi.updateSpeaker(speakerId, payload),
    onSuccess: () => {
      toast.success(t("messages.updateSuccess"));
      invalidate();
    },
    onError,
  });

  const deleteSampleMutation = useMutation({
    mutationFn: (sampleId: number) => voiceprintApi.deleteSample(sampleId),
    onSuccess: () => {
      toast.success(t("messages.sampleDeleted"));
      invalidate();
    },
    onError,
  });

  const speaker = data?.speaker;
  const dirty =
    (name !== null && name !== speaker?.name) ||
    (role !== null && role !== speaker?.role) ||
    (notes !== null && notes !== speaker?.notes) ||
    (threshold !== null && threshold !== String(speaker?.threshold ?? ""));

  const save = () => {
    const payload: Record<string, unknown> = {};
    if (name !== null && name !== speaker?.name) payload.name = name;
    if (role !== null && role !== speaker?.role) payload.role = role;
    if (notes !== null && notes !== speaker?.notes) payload.notes = notes;
    if (threshold !== null && threshold !== String(speaker?.threshold ?? "")) {
      payload.threshold = threshold.trim() === "" ? null : Number(threshold);
    }
    if (Object.keys(payload).length) updateMutation.mutate(payload);
  };

  return (
    <Modal open onClose={onClose} title={speaker?.name || speaker?.speaker_key || "…"} width="max-w-2xl">
      {isLoading || !speaker ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : (
        <div className="space-y-4 text-sm">
          <div className="flex items-center gap-2">
            <Badge variant={speaker.status === "confirmed" ? "ok" : "warn"}>
              {t(`status.${speaker.status}`)}
            </Badge>
            <span className="text-xs text-muted">{speaker.speaker_key}</span>
            <span className="text-xs text-muted">
              {t("fields.effectiveThreshold")}: {data?.effective_threshold}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs text-muted">
            <span>{t("fields.totalAudio")}: {formatDuration(speaker.total_audio_ms)}</span>
            <span>{t("fields.matches")}: {speaker.match_count}</span>
            <span>{t("fields.firstSeen")}: {formatNs(speaker.first_seen_ns)}</span>
            <span>{t("fields.lastSeen")}: {formatNs(speaker.last_seen_ns)}</span>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <Input
              placeholder={t("fields.name")}
              value={name ?? speaker.name}
              onChange={(e) => setName(e.target.value)}
            />
            <Input
              placeholder={t("fields.rolePlaceholder")}
              value={role ?? speaker.role}
              onChange={(e) => setRole(e.target.value)}
            />
            <Input
              placeholder={t("fields.thresholdPlaceholder")}
              value={threshold ?? String(speaker.threshold ?? "")}
              onChange={(e) => setThreshold(e.target.value)}
            />
            <Button
              size="sm"
              disabled={!dirty}
              loading={updateMutation.isPending}
              onClick={save}
            >
              {t("common:save")}
            </Button>
          </div>
          <Textarea
            rows={2}
            placeholder={t("fields.notes")}
            value={notes ?? speaker.notes}
            onChange={(e) => setNotes(e.target.value)}
          />

          {/* 样本池 */}
          <div>
            <div className="mb-1 text-xs font-medium text-muted">
              {t("fields.samplePool")}（{data?.samples.length ?? 0}）
            </div>
            <div className="space-y-1">
              {(data?.samples ?? []).map((s) => (
                <div
                  key={s.id}
                  className="flex items-center gap-3 rounded-md border border-border bg-elevated px-2 py-1 text-xs"
                >
                  <span className="text-muted">#{s.id}</span>
                  <Badge variant="neutral">{s.source || "auto"}</Badge>
                  <span className="text-muted">{t("fields.score")}: {s.score.toFixed(3)}</span>
                  <span className="text-muted">{formatNs(s.created_ns)}</span>
                  <div className="flex-1" />
                  <Button
                    size="sm" variant="ghost"
                    onClick={() => deleteSampleMutation.mutate(s.id)}
                  >
                    <Trash2 size={12} />
                  </Button>
                </div>
              ))}
            </div>
          </div>

          {/* 近期话语 */}
          <div>
            <div className="mb-1 text-xs font-medium text-muted">{t("fields.recentSegments")}</div>
            <div className="space-y-1">
              {(data?.recent_segments ?? []).length === 0 ? (
                <p className="text-xs text-muted">{t("empty.segments")}</p>
              ) : (
                (data?.recent_segments ?? []).map((seg) => (
                  <div key={seg.id} className="rounded-md border border-border px-2 py-1 text-xs">
                    <span className="text-muted">
                      {formatNs(seg.ts_ns)} [{formatOffset(seg.start_ms)}-{formatOffset(seg.end_ms)}]
                    </span>
                    <p className="text-foreground">{seg.transcript || t("fields.noTranscript")}</p>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
