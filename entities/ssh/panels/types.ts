export type SshStatus = "disconnected" | "connecting" | "connected" | "error";

export interface SshConnection {
  name: string;
  host: string;
  port: number;
  username: string;
  description: string;
  status: SshStatus;
  last_error: string;
  connected_at: number;
  last_used_at: number;
  is_default: boolean;
  has_password: boolean;
  has_key: boolean;
}

export interface SshConnectionListResult {
  default: string;
  connections: SshConnection[];
}

export interface SshConnectionCreateRequest {
  name: string;
  host: string;
  port?: number;
  username: string;
  password?: string;
  key_path?: string;
  passphrase?: string;
  description?: string;
}

export interface SshConnectionUpdateRequest {
  name?: string;
  host?: string;
  port?: number;
  username?: string;
  password?: string | null;
  key_path?: string | null;
  passphrase?: string | null;
  description?: string;
}

export interface SshExecRequest {
  command: string;
  timeout?: number;
  work_dir?: string;
}

export interface SshExecResult {
  ok: boolean;
  exit_code: number;
  stdout: string;
  stderr: string;
  connection: string;
  truncated: boolean;
}

export interface SshStatusEvent {
  event: string;
  name: string;
  connection: SshConnection;
}
