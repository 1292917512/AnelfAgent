export interface ApprovalHistoryItem {
  id: number;
  ts_ns: number;
  tool_name: string;
  outcome: string;
  decided_by: string;
  reason: string;
  channel_id: string;
  chat_id: string;
  user_id: string;
  risk_level: string;
  matched_rule: string;
  args_json: string;
}

export interface ApprovalHistoryResponse {
  history: ApprovalHistoryItem[];
  offset: number;
  limit: number;
}

export interface ApprovalStats {
  total: number;
  by_outcome: Record<string, number>;
}

export interface PermissionRuleItem {
  id: string;
  pattern: string;
  effect: string;
  scope: string;
  users: string[];
  risk_level: string;
  description: string;
  enabled: boolean;
  created_by: string;
  created_at: number;
}

export interface ApprovalRulesResponse {
  default_effect: string;
  default_risk: string;
  load_error: string;
  rules: PermissionRuleItem[];
}
