export default function IngestGuide() {
  return (
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
  );
}
