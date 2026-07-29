export interface AuthStatus {
  required: boolean;
  authenticated: boolean;
}

export interface ApiKeyInfo {
  id: string;
  name: string;
  key_prefix: string;
  masked_key: string;
  created_at: number;
  last_used_at: number | null;
}

export interface ApiKeyCreated extends ApiKeyInfo {
  api_key: string;
}
