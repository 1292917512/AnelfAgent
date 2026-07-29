export interface ApprovalPendingItem {
  request_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  risk_level: string;
  reason: string;
  requester_channel: string;
  requester_chat_id: string;
  requester_user_id: string;
  expires_at: number;
  created_at: number;
  matched_rule: string;
}

export interface ApprovalPendingResponse {
  pending: ApprovalPendingItem[];
}

export interface ApprovalHistoryItem {
  request_id: string;
  tool_name: string;
  risk_level: string;
  decision: string;
  decided_by: string;
  decided_at: number;
  decision_reason: string;
  requester_user_id: string;
  requester_channel: string;
  matched_rule: string;
}

export interface ApprovalHistoryResponse {
  history: ApprovalHistoryItem[];
}

export interface ApprovalStats {
  pending_count: number;
  history_size: number;
  history_by_decision: Record<string, number>;
}

export interface ApprovalPolicyItem {
  tool_name_pattern: string;
  risk_level: string;
  requires_approval: boolean;
  timeout_seconds: number;
  on_timeout: string;
  trust_after_n_approvals: number;
  auto_approve_users: string[];
  auto_deny_users: string[];
  description: string;
}

export interface ApprovalPoliciesResponse {
  policies: ApprovalPolicyItem[];
}

export interface PermissionRuleItem {
  id: string;
  pattern: string;
  effect: string;
  scope: string;
  users: string[];
  risk_level: string;
  timeout_seconds: number;
  on_timeout: string;
  trust_after_n_approvals: number;
  description: string;
  enabled: boolean;
  created_by: string;
  created_at: number;
}

export interface ApprovalRulesResponse {
  default_effect: string;
  rules: PermissionRuleItem[];
  persisted_count: number;
  session_count: number;
}

/** PUT /approvals/policies 请求体（策略配置整体保存） */
export interface ApprovalPoliciesPayload {
  policies: ApprovalPolicyItem[];
  [key: string]: unknown;
}
