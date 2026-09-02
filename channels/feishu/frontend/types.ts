import type { ChannelTestSendResult } from "@/lib/types/channels";

/** 飞书测试发送结果（核心 ChannelTestSendResult + 飞书发送管道的附加字段） */
export interface FeishuTestSendResult extends ChannelTestSendResult {
  message_ids?: string[];
  rendered_as?: string;
  chunks?: number;
  hint?: string;
  cause?: string;
}
