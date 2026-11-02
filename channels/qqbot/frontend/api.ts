import { api } from "@/lib/api";

export interface SetupInfo {
  callback_path: string;
  intents: Record<string, number>;
  status: string;
  mode: string | null;
  self_id: string;
  bot_name: string;
  last_event_at: number;
  last_error: string;
}

export async function getSetup(): Promise<SetupInfo> {
  return (await api.get<SetupInfo>("/channels/qqbot/setup")).data;
}
