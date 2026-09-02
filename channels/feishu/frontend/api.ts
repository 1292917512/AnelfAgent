/**
 * 飞书频道前端 API — 直接复用核心 adaptersApi（/adapters 管理面），
 * 本文件仅保留语义别名，避免插件间重复封装。
 */
import { adaptersApi } from "@/lib/api";

export const feishuApi = {
  listAdapters: () => adaptersApi.list(),
  health: () => adaptersApi.testHealth("feishu"),
  testSend: (chatId: string, text: string) =>
    adaptersApi.testSend("feishu", { chat_id: chatId, text }),
};
