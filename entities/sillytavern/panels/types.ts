/** SillyTavern 酒馆管理实体（/api/entity/sillytavern）类型定义 */

/** 运行状态（GET /status、POST /start|/stop|/restart 返回结构） */
export interface StStatus {
  running: boolean;
  managed: boolean;
  url: string;
  port: number;
  pid: number | null;
  started_at: string | null;
  uptime_sec: number | null;
  version: string;
  commit: string;
  log_file: string;
  auto_start: boolean;
}

export interface StLogsResult {
  log_tail: string;
}

/** 实体配置（GET/POST /config） */
export interface StConfig {
  st_dir: string;
  port: number;
  listen: boolean;
  disable_csrf: boolean;
  extra_args: string;
  auto_start: boolean;
  context_inject: boolean;
  context_max_tokens: number;
  startup_timeout: number;
}

export interface StGitInfo {
  branch: string;
  last_commit: string;
  remotes: string[];
  dirty_files: string[];
  dirty_count: number;
}

export interface StGitVersion {
  name: string;
  commit: string;
  current: boolean;
}

export interface StGitRemoteDetail {
  name: string;
  url: string;
}

export interface StGitVersionsResult {
  remote: string;
  current_branch: string;
  current_commit: string;
  remotes: StGitRemoteDetail[];
  versions: StGitVersion[];
  fetch_hint: string;
}

export interface StGitCheckoutResult {
  ok: boolean;
  branch?: string;
  commit?: string;
  running?: boolean;
  hint?: string;
  error?: string;
}

export interface StGitUpdateResult {
  ok: boolean;
  output: string;
}

export interface StGitCommitResult {
  ok: boolean;
  commit: string;
}

/** 角色卡（列表概要） */
export interface StCharacter {
  name: string;
  avatar: string;
  description: string;
  personality: string;
  first_mes: string;
  tags: string[];
  fav: boolean;
}

export interface StCharacterListResult {
  count: number;
  characters: StCharacter[];
}

export interface StCharacterCreatePayload {
  name: string;
  description: string;
  personality: string;
  first_mes: string;
  scenario: string;
  mes_example: string;
  system_prompt: string;
  tags: string[];
}

export interface StCharacterEditPayload {
  avatar: string;
  field: string;
  value: unknown;
  current_name?: string;
}

export interface StCharacterDeletePayload {
  avatar: string;
  delete_chats: boolean;
}

/** settings.json 模型相关片段 */
export interface StSettings {
  main_api?: string;
  temperature?: number;
  max_context?: number;
  max_tokens?: number;
  [key: string]: unknown;
}

export interface StModelUpdatePayload {
  main_api?: string;
  model?: string;
  temperature?: number;
  max_context?: number;
  max_tokens?: number;
}

export interface StModelUpdateResult {
  ok: boolean;
  changed: string[];
}

export interface StChatsResult {
  chats: string[];
}

export interface StChatMessagesResult {
  messages: StChatMessage[];
}

/** AnelfAgent 已配置的可对话模型（供直连酒馆） */
export interface StMyModel {
  provider_id: string;
  provider_name: string;
  model_id: string;
  model: string;
  base_url: string;
  api_type: string;
}

export interface StMyModelsResult {
  models: StMyModel[];
}

export interface StUseMyModelResult {
  ok: boolean;
  model: string;
  endpoint: string;
  provider: string;
}

/** AI 对话发送结果（走 anelf-bridge 插件） */
export interface StChatSendResult {
  ok: boolean;
  chat_file: string;
  character: string;
  reply: string;
  message_count: number;
}

export interface StChatMessage {
  name?: string;
  is_user?: boolean;
  is_system?: boolean;
  send_date?: string;
  mes?: string;
}
