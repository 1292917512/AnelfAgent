/** NoneBot 桥接管理接口类型（/api/nonebot） */

export interface NoneBotEnvKeyMeta {
  key: string;
  label: string;
  secret: boolean;
  json_mode: boolean;
  placeholder: string;
}

export interface NoneBotAdapterSetup {
  difficulty: string;
  env_keys: NoneBotEnvKeyMeta[];
  notes: string;
  docs: string;
}

export interface NoneBotAdapterInfo {
  key: string;
  label: string;
  package: string;
  module: string;
  builtin: boolean;
  version: string;
  setup: NoneBotAdapterSetup;
  installed: boolean;
  enabled: boolean;
}

export interface NoneBotWorkerProcess {
  alive: boolean;
  pid: number | null;
  returncode: number | null;
  started_at: number | null;
  want_running: boolean;
  auto_restart: boolean;
  restart_count: number;
  venv_ready: boolean;
  uv: string | null;
}

export interface NoneBotBotInfo {
  bot_id: string;
  adapter: string;
}

export interface NoneBotPluginInfo {
  module: string;
  name: string;
  description: string;
  usage: string;
  type: string | null;
  homepage: string | null;
  supported_adapters: string[] | null;
  matcher_count: number;
}

export interface NoneBotWorkerSnapshot {
  nonebot_version: string;
  adapters: string[];
  bots: NoneBotBotInfo[];
  plugins: NoneBotPluginInfo[];
}

export interface NoneBotChannelStatus {
  key: string;
  name: string;
  status: string;
  bridge_connected: boolean;
  worker: NoneBotWorkerProcess;
  worker_snapshot: NoneBotWorkerSnapshot;
  worker_base_url: string;
  intercept_all: boolean;
}

export interface NoneBotInstallState {
  running: boolean;
  packages: string[];
  logs: string[];
  error: string;
  ok: boolean | null;
  uninstall?: boolean;
  finished_at?: number;
}

export interface NoneBotStatus {
  ready: boolean;
  enabled: boolean;
  registered: boolean;
  channel_status: NoneBotChannelStatus | null;
  install: NoneBotInstallState;
  env?: NoneBotStatusEnv;
  error?: string;
}

export interface NoneBotStatusEnv {
  venv_ready: boolean;
  uv: string;
  uv_found: boolean;
}

export interface NoneBotEnvStatus {
  venv_ready: boolean;
  uv_found: boolean;
  uv_version: string;
  python_version: string;
  baseline: string[];
  venv_path: string;
  runtime_dir: string;
  install: NoneBotInstallState;
}

export interface NoneBotPackageInfo {
  name: string;
  version: string;
}

export interface NoneBotPackagesResult {
  success: boolean;
  count: number;
  packages: NoneBotPackageInfo[];
}

export interface NoneBotConfig {
  enabled: boolean;
  adapters: string[];
  plugins: string[];
  nonebot_env: Record<string, string>;
  intercept_all: boolean;
  bridge_ws_port: number;
  worker_host: string;
  worker_port: number;
  auto_restart: boolean;
  pip_index_url?: string;
  pip_proxy?: string;
  package_specs?: Record<string, string>;
}

export interface NoneBotStorePlugin {
  module_name: string;
  project_link: string;
  name: string;
  desc: string;
  author: string;
  homepage: string;
  tags: { label: string; color?: string }[];
  is_official: boolean;
  type: string | null;
  supported_adapters: string[] | null;
  valid: boolean;
  version: string;
}

export interface NoneBotStorePluginsResult {
  count: number;
  plugins: NoneBotStorePlugin[];
}

export interface NoneBotPluginsResult {
  success: boolean;
  plugins?: NoneBotPluginInfo[];
  error?: string;
}

/** 后端统一 JSON 结果（success + 任意字段） */
export interface NoneBotOpResult {
  success: boolean;
  error?: string;
  message?: string;
  restarted?: boolean;
  [k: string]: unknown;
}

export interface NoneBotSourceItem {
  key: string;
  spec: string;
  kind: "git" | "path" | string;
  repo_path: string;
  repo_exists: boolean;
}

export interface NoneBotSourcesResult {
  sources_dir: string;
  items: NoneBotSourceItem[];
}
