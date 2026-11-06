export interface InboxRecord {
  id: string;
  state: string;
  attempts: number;
  error: string;
  created_at: number;
}

export interface GitHubStatus {
  status?: string;
  enabled: boolean;
  poller_running?: boolean;
  webhook_running?: boolean;
  rate_remaining?: number;
  dispatched_events?: number;
  inbox?: { counts: Record<string, number>; events: InboxRecord[] };
  repo_states?: Record<string, { last_error?: string; possible_gap?: boolean; paused_until?: number }>;
}
