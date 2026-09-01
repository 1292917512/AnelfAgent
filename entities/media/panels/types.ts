/** 媒体库（entities/media）类型定义。 */

/** provider 能力状态（/api/entity/media/providers 响应元素）。 */
export interface MediaProviderStatus {
  name: string;
  capabilities: string[];
  /** 各能力是否已配置就绪（凭据/模型） */
  configured: Record<string, boolean>;
}

/** 媒体库配置（/api/entity/media/config 响应）。 */
export interface MediaConfig {
  /** 各能力的 provider 优先级链，如 { tts: ["models", "minimax"] } */
  provider_priority: Record<string, string[]>;
  default_voice: string;
  default_reference_audio: string;
  default_reference_text: string;
  defaults: {
    image_size: string;
    video_resolution: string;
    video_duration: number;
  };
  style_presets: Record<string, string>;
}

/** /api/entity/media/providers 响应。 */
export interface MediaProvidersResult {
  providers: MediaProviderStatus[];
  provider_priority: Record<string, string[]>;
}
