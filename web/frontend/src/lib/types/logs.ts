export interface LogEntry {
  level: string;
  message: string;
  tag: string;
  time: string;
}

export interface LogStats {
  total: number;
  capacity: number;
  by_level: Record<string, number>;
  by_tag: Record<string, number>;
}
