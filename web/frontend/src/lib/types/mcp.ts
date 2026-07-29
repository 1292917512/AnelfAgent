export type MCPTransport = "stdio" | "streamable_http" | "sse";

export interface MCPServer {
  name: string;
  /** 展示用地址（stdio 为命令），后端已脱敏 */
  url: string;
  transport: MCPTransport | string;
  enabled: boolean;
  connected: boolean;
  tool_count: number;
  tools: string[];
  last_error: string;
}

/** MCP server 完整配置（创建/编辑共用，字段均可选） */
export interface MCPServerConfig {
  url?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  headers?: Record<string, string>;
  transport?: MCPTransport;
  enabled?: boolean;
  timeout?: number;
  sse_read_timeout?: number;
  call_timeout?: number;
}

export interface MCPToolParam {
  name: string;
  description: string;
  type: string;
  required: boolean;
  enum: string[] | null;
}

export interface MCPToolInfo {
  name: string;
  description: string;
  params: MCPToolParam[];
}

export interface MCPToggleResult {
  success: boolean;
  message: string;
  tool_count?: number;
}
