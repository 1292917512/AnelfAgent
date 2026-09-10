/** AI 桌面组件配置项（后端 framework.DesktopModule.describe 序列化） */
export interface ModuleConfigItem {
  key: string;
  name: string;
  description: string;
  value: unknown;
  default: unknown;
  value_type: string;
  unit: string;
  min: number | null;
  max: number | null;
  advanced: boolean;
}

/** 天气组件单个地区的结构化状态 */
export interface LocationWeather {
  name: string;
  enabled: boolean;
  has_data: boolean;
  error?: string | null;
  /** 检索确认时落库的完整标签（名称·行政区·国家） */
  configured_label?: string;
  label?: string;
  condition?: string;
  temp?: number | null;
  feels?: number | null;
  humidity?: number | null;
  wind_speed?: number | null;
  wind_direction?: string;
  today_min?: number | null;
  today_max?: number | null;
  forecast?: DailyForecast[];
  fetched_at?: number;
}

/** 天气逐日预报条目 */
export interface DailyForecast {
  date: string;
  condition: string;
  tmin?: number | null;
  tmax?: number | null;
  precip_prob?: number | null;
}

/** 日历组件的单个日程/标注 */
export interface CalendarEvent {
  id: string;
  title: string;
  date: string;
  time?: string | null;
  end_time?: string | null;
  kind: "event" | "note";
  note?: string;
  remind_minutes?: number | null;
  source?: string;
}

/** 日历组件的订阅源状态 */
export interface CalendarSubscription {
  name: string;
  enabled: boolean;
  ok?: boolean;
  count?: number;
  error?: string;
  synced_at?: number;
}

/** 地区检索候选（Open-Meteo Geocoding） */
export interface GeocodeCandidate {
  name: string;
  label: string;
  latitude: number;
  longitude: number;
  admin1: string;
  country: string;
}

/** 日期时间组件的额外时区状态 */
export interface ExtraZone {
  timezone: string;
  valid: boolean;
  datetime?: string;
  weekday?: string;
}

/** 订阅额度组件单个用量窗口 */
export interface QuotaWindow {
  label: string;
  remaining_percent?: number | null;
  used?: number | null;
  limit?: number | null;
  remaining?: number | null;
  /** 重置时间（秒级时间戳） */
  reset_at?: number | null;
}

/** 订阅额度组件单个供应商状态 */
export interface SubscriptionProvider {
  key: string;
  name: string;
  /** 凭据来源（llm_clients.json 供应商 id） */
  source?: string | null;
  state: "ok" | "pending" | "error" | "no_credential";
  plan?: string | null;
  windows?: QuotaWindow[];
  error?: string;
  fetched_at?: number;
}

/** 组件结构化详情（各组件字段的并集，全部可选；面板按 key 特化渲染） */
export interface ModuleDetail {
  // datetime 组件
  datetime?: string;
  weekday?: string;
  timezone?: string;
  festivals_today?: string[];
  next_holiday?: { name: string; date: string; days: number } | null;
  extra_zones?: ExtraZone[];
  // weather 组件
  locations?: LocationWeather[];
  refresh_minutes?: number;
  // subscription 组件
  providers?: SubscriptionProvider[];
  // calendar 组件
  upcoming?: CalendarEvent[];
  subscriptions?: CalendarSubscription[];
  local_count?: number;
  sync_minutes?: number;
}

/** AI 桌面组件状态 */
export interface DesktopModuleInfo {
  key: string;
  display_name: string;
  description: string;
  priority: number;
  enabled: boolean;
  default_enabled: boolean;
  refresh_interval: number;
  last_refresh: number | null;
  last_error: string;
  configs: ModuleConfigItem[];
  /** 组件当前的注入文本（未启用/无内容时为 null） */
  current_content: string | null;
  /** 组件结构化状态详情 */
  detail: ModuleDetail;
}

export interface ModulesResponse {
  modules: DesktopModuleInfo[];
  count: number;
}

export interface PreviewResponse {
  injecting: boolean;
  content: string;
}

export interface RefreshResponse {
  success: boolean;
  last_error: string | null;
  content: string | null;
}
