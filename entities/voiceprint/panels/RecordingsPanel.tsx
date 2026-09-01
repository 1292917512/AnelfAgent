import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ChevronDown, ChevronRight, FileAudio, Folder, RefreshCw, Trash2 } from "lucide-react";
import { voiceprintApi } from "./api";
import type { Recording } from "./types";
import { Badge, Button, ConfirmDialog, EmptyState, Spinner, toast } from "@/components/ui";
import { formatNs, formatOffset } from "./format";

/** 录制归组：按录制时间展示 NAS 同步的录制单元（文件夹名隐藏，时间即标题）。 */
export function RecordingsPanel() {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Recording | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["voiceprintRecordings"],
    queryFn: () => voiceprintApi.recordings({ limit: 100 }).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["voiceprintRecordings"] });
    queryClient.invalidateQueries({ queryKey: ["voiceprintSegments"] });
    queryClient.invalidateQueries({ queryKey: ["voiceprintStats"] });
    queryClient.invalidateQueries({ queryKey: ["voiceprintSyncPreview"] });
  };

  const onError = (err: unknown) => {
    const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
    toast.error(msg || t("messages.opFailed"));
  };

  const deleteMutation = useMutation({
    mutationFn: (path: string) => voiceprintApi.deleteRecording(path),
    onSuccess: () => {
      toast.success(t("messages.deleteSuccess"));
      setDeleteTarget(null);
      invalidate();
    },
    onError,
  });

  const rebuildMutation = useMutation({
    mutationFn: (path: string) => voiceprintApi.rebuildRecordings([path]),
    onSuccess: (r) => {
      const outcome = r.data.results[0];
      if (r.data.error) toast.error(r.data.error);
      else if (outcome?.outcome === "done") toast.success(t("messages.rebuildSuccess"));
      else toast.success(t(`recordings.rebuildOutcome.${outcome?.outcome ?? "done"}`));
      invalidate();
    },
    onError,
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-2">
      {isLoading ? (
        <div className="flex justify-center py-10"><Spinner /></div>
      ) : items.length === 0 ? (
        <EmptyState title={t("empty.recordings")} description={t("empty.recordingsHint")} />
      ) : (
        items.map((rec) => (
          <RecordingCard
            key={rec.path}
            recording={rec}
            expanded={expanded === rec.path}
            onToggle={() => setExpanded(expanded === rec.path ? null : rec.path)}
            onDelete={() => setDeleteTarget(rec)}
            onRebuild={() => rebuildMutation.mutate(rec.path)}
            rebuilding={rebuildMutation.isPending && rebuildMutation.variables === rec.path}
          />
        ))
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.path)}
        title={t("recordings.deleteTitle")}
        message={t("recordings.deleteHint", {
          time: formatNs(deleteTarget?.started_ns ?? 0),
        })}
        danger
        loading={deleteMutation.isPending}
      />
    </div>
  );
}

function RecordingCard({
  recording,
  expanded,
  onToggle,
  onDelete,
  onRebuild,
  rebuilding,
}: {
  recording: Recording;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onRebuild: () => void;
  rebuilding: boolean;
}) {
  const { t } = useTranslation("voiceprint");

  const { data: segments, isLoading } = useQuery({
    queryKey: ["voiceprintRecordingSegments", recording.path],
    queryFn: () =>
      voiceprintApi.segments({ recording_path: recording.path, limit: 100 })
        .then((r) => r.data),
    enabled: expanded,
  });

  return (
    <div
      className={`rounded-lg border border-border bg-card ${
        recording.status === "no_speech" ? "opacity-70" : ""
      }`}
    >
      <div className="flex items-center gap-2 px-3 py-2">
        <Button size="sm" variant="ghost" onClick={onToggle}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </Button>
        {recording.kind === "folder" ? <Folder size={14} /> : <FileAudio size={14} />}
        <span className="text-sm font-medium text-foreground">
          {formatNs(recording.started_ns)}
        </span>
        <Badge
          variant={
            recording.status === "done" ? "ok"
              : recording.status === "no_speech" ? "neutral" : "danger"
          }
        >
          {t(`sync.status.${recording.status}`)}
        </Badge>
        <span className="text-xs text-muted">
          {recording.file_count} {t("recordings.files")} · {recording.segments} {t("sync.segments")}
        </span>
        {recording.status === "no_speech" && (
          <span className="text-xs text-muted">{t("recordings.noSpeechHint")}</span>
        )}
        {recording.error && (
          <span className="text-xs text-danger truncate max-w-48">{recording.error}</span>
        )}
        <div className="flex-1" />
        <Button
          size="sm" variant="ghost"
          title={t("recordings.rebuild")}
          disabled={rebuilding}
          onClick={onRebuild}
        >
          <RefreshCw size={13} className={rebuilding ? "animate-spin" : ""} />
        </Button>
        <Button size="sm" variant="ghost" onClick={onDelete}>
          <Trash2 size={13} />
        </Button>
      </div>

      {expanded && (
        <div className="border-t border-border px-3 py-2 space-y-1">
          {isLoading ? (
            <div className="flex justify-center py-4"><Spinner /></div>
          ) : (segments?.items ?? []).length === 0 ? (
            <p className="text-xs text-muted py-2">{t("empty.segments")}</p>
          ) : (
            segments!.items.map((seg) => (
              <div key={seg.id} className="rounded-md border border-border px-2 py-1 text-xs">
                <div className="flex items-center gap-2 text-muted">
                  <span>{formatNs(seg.ts_ns)}</span>
                  <span>[{formatOffset(seg.start_ms)}-{formatOffset(seg.end_ms)}]</span>
                  <Badge variant={seg.is_new_speaker ? "warn" : "accent"}>
                    {seg.speaker_name || seg.speaker_key || t("fields.unknownSpeaker")}
                  </Badge>
                </div>
                <p className="text-foreground">{seg.transcript || t("fields.noTranscript")}</p>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
