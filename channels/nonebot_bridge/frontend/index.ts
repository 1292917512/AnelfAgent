/**
 * NoneBot 桥接频道前端插件 — 整页管理面（worker/环境/适配器/插件/商店/日志）。
 *
 * 插件契约（src/lib/channel-plugins.ts）：route 声明整页路由（App 动态注册），
 * hiddenInChannelList 从频道列表隐藏（管理面即本页）；删除本目录即从 WebUI
 * 完整拔出（页面 + 导航 + 频道卡片）。
 */
import { registerPluginI18n } from "@/lib/plugin-i18n";
import type { ChannelPlugin } from "@/lib/channel-plugins";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

registerPluginI18n("nonebot", { zh, en });

export default {
  route: { path: "nonebot" },
  page: () => import("./Page"),
  hiddenInChannelList: true,
} satisfies ChannelPlugin;
