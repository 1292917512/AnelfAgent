export interface FunasrStatus {
  configured: boolean;
  reachable: boolean;
  endpoint: string;
}

export interface GpuWorkerStatus {
  ok?: boolean;
  loaded?: boolean;
  model?: string;
  device?: string;
  vram_gb?: number;
  cuda?: boolean;
  pipeline?: boolean;
}

export interface GpuStatus {
  moss?: GpuWorkerStatus;
  asr?: GpuWorkerStatus;
  diarize?: GpuWorkerStatus;
  embedder?: GpuWorkerStatus;
}

export interface GpuUnloadResult {
  [target: string]: {
    ok?: boolean;
    vram_gb_before?: number;
    vram_gb_after?: number;
    error?: string;
  };
}
