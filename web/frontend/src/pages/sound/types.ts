/** 音频页类型别名：统一从 lib/api 的音频类型再导出（面板内部引用保持简短名）。 */

import type {
  AudioConsolidateResult,
  AudioIdentifyCandidate,
  AudioIdentifyResult,
  AudioRecording,
  AudioRecordingListResult,
  AudioSegment,
  AudioSegmentListResult,
  AudioSimilarityMapResult,
  AudioSpeaker,
  AudioSpeakerDetail,
  AudioSpeakerListResult,
  AudioSpeakerUpdatePayload,
} from "@/lib/api";

export type Speaker = AudioSpeaker;
export type SpeakerListItem = AudioSpeaker;
export type SpeakerListResult = AudioSpeakerListResult;
export type SpeakerDetail = AudioSpeakerDetail;
export type SpeakerUpdatePayload = AudioSpeakerUpdatePayload;
export type VoiceSample = AudioSpeakerDetail["samples"][number];
export type VoiceSegment = AudioSegment;
export type SegmentListResult = AudioSegmentListResult;
export type IdentifyCandidate = AudioIdentifyCandidate;
export type { AudioIdentifyResult };
export type ConsolidateResult = AudioConsolidateResult;
export type SimilarityMapResult = AudioSimilarityMapResult;
export type Recording = AudioRecording;
export type RecordingListResult = AudioRecordingListResult;
