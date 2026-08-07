import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { FolderSync, Upload } from "lucide-react";
import { voiceprintApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Badge, Button, Spinner, Switch, toast } from "@/components/ui";
import { formatNs, formatOffset } from "./format";

/** 入库与识别：手动操作台（状态条 + 上传试识别/入库 + 对接契约）。
 *  状态总览在「总览」tab，此处仅保留紧凑状态条便于操作时对齐上下文。 */
export function IngestPanel() {
  const { t } = useTranslation("voiceprint");
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [ingest, setIngest] = useState(false);

  const { data: stats, refetch } = useQuery({
    queryKey: ["voiceprintStats"],
    queryFn: () => voiceprintApi.stats().then((r) => r.data),
    refetchInterval: 10_000,
  });

  const { data: openlist } = useQuery({
    queryKey: ["voiceprintOpenlistStatus"],
    queryFn: () => voiceprintApi.openlistStatus().then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: preview } = useQuery({
    queryKey: ["voiceprintSyncPreview"],
    queryFn: () => voiceprintApi.syncPreview().then((r) => r.data),
    refetchInterval: 15_000,
  });

  const syncMutation = useMutation({
    mutationFn: () => voiceprintApi.syncNow(),
    onSuccess: (r) => {
      const d = r.data;
      if (d.error) toast.error(d.error);
      else toast.success(t("messages.syncDone", { new: d.new, ingested: d.ingested }));
      refetch();
      queryClient.invalidateQueries({ queryKey: ["voiceprintSyncPreview"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintRecordings"] });
      queryClient.invalidateQueries({ queryKey: ["voiceprintSpeakers"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.opFailed"));
    },
  });

  const identifyMutation = useMutation({
    mutationFn: () => voiceprintApi.identifyAudio(file!, ingest),
    onSuccess: () => {
      if (ingest) {
        toast.success(t("messages.ingestSuccess"));
        refetch();
        queryClient.invalidateQueries({ queryKey: ["voiceprintSpeakers"] });
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.opFailed"));
    },
  });

  const result = identifyMutation.data?.data;
  const watch = stats?.watch;

  return (
    <div className="space-y-4">
      {/* 紧凑状态条：对接与同步关键状态一览 */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant={stats?.funasr_configured ? "ok" : "warn"}>
          FunASR {stats?.funasr_configured ? "✓" : t("stats.notConfigured")}
        </Badge>
        <Badge variant={watch?.enabled ? "ok" : "neutral"}>
          {watch?.enabled ? t("sync.enabled") : t("sync.disabled")}
        </Badge>
        {openlist?.configured && (
          <Badge variant={openlist.reachable ? "ok" : "danger"}>
            OpenList {openlist.reachable ? `${openlist.latency_ms}ms` : "✗"}
          </Badge>
        )}
        <Badge variant={stats?.ingest_enabled ? "ok" : "neutral"}>
          ingest {stats?.ingest_enabled ? "✓" : t("stats.notConfigured")}
        </Badge>
        {preview && !preview.error && (
          <Badge variant={preview.pending.length ? "warn" : "ok"}>
            {t("overview.pendingTitle")} {preview.pending.length}
          </Badge>
        )}
        <span className="text-muted">
          {t("sync.lastScan")}: {formatNs(watch?.last_scan_ns ?? 0)}
        </span>
        <div className="flex-1" />
        <Button
          size="sm" variant="secondary"
          loading={syncMutation.isPending}
          disabled={!stats?.funasr_configured}
          onClick={() => syncMutation.mutate()}
        >
          <FolderSync size={14} className="mr-1" />
          {t("sync.runNow")}
        </Button>
      </div>

      {/* 上传试识别 */}
      <Card title={t("ingest.tryTitle")} subtitle={t("ingest.trySubtitle")}>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="file"
              accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.amr,.wma"
              className="text-sm text-muted file:mr-3 file:rounded-md file:border file:border-border file:bg-elevated file:px-3 file:py-1.5 file:text-sm file:text-foreground"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <label className="flex items-center gap-2 text-sm text-muted">
              <Switch checked={ingest} onChange={setIngest} />
              {t("ingest.ingestAfterIdentify")}
            </label>
            <Button
              disabled={!file || !stats?.funasr_configured}
              loading={identifyMutation.isPending}
              onClick={() => identifyMutation.mutate()}
            >
              <Upload size={14} className="mr-1" />
              {t("ingest.run")}
            </Button>
          </div>
          {!stats?.funasr_configured && (
            <p className="text-xs text-muted">{t("ingest.needFunasr")}</p>
          )}

          {identifyMutation.isPending && (
            <div className="flex justify-center py-6"><Spinner /></div>
          )}

          {result && !identifyMutation.isPending && (
            <div className="space-y-2">
              {result.segments.length === 0 ? (
                <p className="text-sm text-muted">{t("ingest.noSpeech")}</p>
              ) : (
                result.segments.map((seg, i) => (
                  <div key={i} className="rounded-md border border-border px-3 py-2 text-sm space-y-1">
                    <div className="text-xs text-muted">
                      [{formatOffset(seg.start_ms)}-{formatOffset(seg.end_ms)}]
                    </div>
                    <p className="text-foreground">{seg.text || t("fields.noTranscript")}</p>
                    <div className="flex flex-wrap gap-1">
                      {seg.candidates.length === 0 ? (
                        <Badge variant="warn">{t("ingest.noCandidate")}</Badge>
                      ) : (
                        seg.candidates.map((c) => (
                          <Badge key={c.id} variant={c.matched ? "ok" : "neutral"}>
                            {c.name || c.speaker_key} {c.similarity.toFixed(3)}
                          </Badge>
                        ))
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </Card>

      {/* 对接说明 */}
      <Card title={t("ingest.pipelineTitle")}>
        <pre className="overflow-x-auto rounded-md bg-elevated p-3 text-xs text-muted whitespace-pre-wrap">
{`POST /api/entity/voiceprint/ingest
X-Ingest-Token: <voiceprint_ingest_token>

{
  "source_file": "/nas/audio/2026-08-06/a.wav",
  "recording_path": "/nas/audio/audio_20260806143300",
  "device_source": "客厅麦克风",
  "ts": 1785988800,
  "segments": [
    {"start_ms": 0, "end_ms": 3200, "text": "……", "vector": [0.12, …(192维)],
     "abs_start_ms": 1786005000000, "abs_end_ms": 1786005003200}
  ]
}

# abs_* 可选：epoch 毫秒绝对时刻（缺省按 ts + 段内偏移换算）`}
        </pre>
      </Card>
    </div>
  );
}
