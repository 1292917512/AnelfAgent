import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Upload } from "lucide-react";
import { audioApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Badge, Button, Spinner, Switch, toast } from "@/components/ui";
import { formatOffset } from "./format";

/** 识别与入库：上传音频试识别（声纹候选）/ 识别并入库 + 外部推送对接契约。 */
export function IdentifyPanel() {
  const { t } = useTranslation("sound");
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [ingest, setIngest] = useState(false);

  const { data: status } = useQuery({
    queryKey: ["audioStatus"],
    queryFn: () => audioApi.status().then((r) => r.data),
    refetchInterval: 10_000,
  });

  const identifyMutation = useMutation({
    mutationFn: () => audioApi.identifyAudio(file!, ingest),
    onSuccess: () => {
      if (ingest) {
        toast.success(t("messages.ingestSuccess"));
        queryClient.invalidateQueries({ queryKey: ["audioSpeakers"] });
        queryClient.invalidateQueries({ queryKey: ["audioStats"] });
      }
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.opFailed"));
    },
  });

  const result = identifyMutation.data?.data;
  const asrReady = status?.asr_available ?? false;

  return (
    <div className="space-y-4 max-w-3xl">
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
              disabled={!file || !asrReady}
              loading={identifyMutation.isPending}
              onClick={() => identifyMutation.mutate()}
            >
              <Upload size={14} className="mr-1" />
              {t("ingest.run")}
            </Button>
          </div>
          {!asrReady && <p className="text-xs text-muted">{t("ingest.needFunasr")}</p>}

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
{`POST /api/entity/audiosync/ingest
X-Ingest-Token: <audiosync_ingest_token>

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
