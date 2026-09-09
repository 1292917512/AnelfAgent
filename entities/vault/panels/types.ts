export interface VaultStatus {
  initialized: boolean;
  unlock_mode: "machine" | "master" | "";
  unlocked: boolean;
  entry_count: number;
  auto_lock_remaining: number;
}

export interface VaultEntry {
  id: string;
  title: string;
  username: string;
  url: string;
  tags: string[];
  favorite: boolean;
  has_password: boolean;
  has_totp: boolean;
  has_notes: boolean;
  created_at: number;
  updated_at: number;
  score?: number | null;
}

export interface VaultEntryList {
  count: number;
  entries: VaultEntry[];
}

export interface VaultEntryCreate {
  title: string;
  username: string;
  url: string;
  password: string;
  totp_secret: string;
  notes: string;
  tags: string[];
  favorite: boolean;
  generate?: boolean;
}

export interface VaultEntryUpdate {
  title?: string;
  username?: string;
  url?: string;
  password?: string;
  totp_secret?: string;
  notes?: string;
  tags?: string[];
  favorite?: boolean;
}

export interface VaultTotp {
  entry_id: string;
  title: string;
  code: string;
  period: number;
  remaining: number;
}

export interface VaultStrength {
  entropy: number;
  level: "weak" | "fair" | "strong" | "excellent";
  issues: string[];
}

export interface VaultGenerateResult {
  password: string;
  strength: VaultStrength;
}

export interface VaultImportResult {
  added: number;
  skipped: number;
  overwritten: number;
  failed: number;
}

export interface VaultBreachReport {
  checked_entries: number;
  pwned: { id: string; title: string; count: number }[];
  reused: { count: number; entries: { id: string; title: string }[] }[];
  weak: { id: string; title: string; entropy: number; issues: string[] }[];
  hibp_checked: boolean;
  hibp_error: string;
}

export type VaultImportFormat =
  | "bitwarden_json"
  | "bitwarden_csv"
  | "chrome_csv"
  | "keepass_csv"
  | "encrypted";

export type VaultExportFormat = "json" | "csv" | "encrypted";
