/**
 * 飞书频道前端插件 — 连接状态面板（健康探针 + 接入指引 + 测试发送）。
 *
 * 插件契约（src/lib/channel-plugins.ts）：本文件为轻量 eager 清单，
 * i18n 自注册 + 组件懒加载；删除本目录即从 WebUI 完整拔出。
 */
import { registerPluginI18n } from "@/lib/plugin-i18n";
import type { ChannelPlugin } from "@/lib/channel-plugins";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

registerPluginI18n("channel-feishu", { zh, en });

export default {
  panel: () => import("./components/FeishuPanel"),
} satisfies ChannelPlugin;
