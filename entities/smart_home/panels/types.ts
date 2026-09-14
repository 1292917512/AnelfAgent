/** 智能家居面板类型定义（对齐后端 schemas/framework 序列化） */

/** 单供应商连接状态（SmartHomeProvider.status） */
export interface ProviderStatus {
  provider: string;
  provider_name: string;
  configured: boolean;
  connected: boolean;
  device_count: number;
  last_error: string | null;
  connected_at: number | null;
  discovery: boolean;
}

/** 连接状态（多供应商聚合，manager.status） */
export interface StatusResponse {
  providers: ProviderStatus[];
  configured: boolean;
  connected: boolean;
  device_count: number;
}

/** 单台设备状态（DeviceState.to_dict） */
export interface DeviceInfo {
  entity_id: string;
  domain: string;
  name: string;
  state: string;
  area: string;
  available: boolean;
  attributes: Record<string, unknown>;
}

/** 设备域控制动作描述（DeviceDomain.describe.actions） */
export interface DomainActionInfo {
  description: string;
  accepts_value: boolean;
  value_hint: string;
}

/** 设备域配置项（DeviceDomain.describe.configs） */
export interface DomainConfigItem {
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

/** 设备域组件描述（DeviceDomain.describe） */
export interface DeviceDomainInfo {
  key: string;
  display_name: string;
  description: string;
  priority: number;
  ha_domains: string[];
  enabled: boolean;
  default_enabled: boolean;
  device_count: number;
  available_count: number;
  actions: Record<string, DomainActionInfo>;
  configs: DomainConfigItem[];
}

export interface DevicesResponse {
  connection: StatusResponse;
  devices: DeviceInfo[];
  count: number;
}

export interface DomainsResponse {
  domains: DeviceDomainInfo[];
  count: number;
}

export interface PreviewResponse {
  injecting: boolean;
  content: string | null;
}

export interface ControlResponse {
  success: boolean;
  entity_id: string;
  name: string;
  action: string;
  service: string;
}

/** 发现设备结果（POST /providers/{key}/discover） */
export interface DiscoverResponse {
  found: Array<{ entity_id: string; name: string }>;
  count: number;
}

/** SSE 事件载荷（manager 广播；connection 事件为单供应商状态） */
export interface StateEventPayload {
  event: string;
  device?: DeviceInfo;
  status?: ProviderStatus;
}
