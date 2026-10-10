import { defineUiContributions } from "@/lib/ui-contributions";

export default defineUiContributions([
  { id: "service-status", slot: "audio.overview.cards", title: { ns: "audiosync", key: "funasr.title" }, load: () => import("./ServiceStatus") },
  { id: "ingest-guide", slot: "audio.identify.after", title: { ns: "audiosync", key: "pipelineTitle" }, load: () => import("./IngestGuide") },
  { id: "rebuild-recording", slot: "audio.recording.actions", title: { ns: "audiosync", key: "recordings.rebuild" }, load: () => import("./RebuildRecording") },
]);
