/**
 * 微信频道前端插件 — 扫码登录。
 *
 * 插件契约（src/lib/channel-plugins.ts）：本文件为轻量 eager 清单，
 * i18n 自注册 + 组件懒加载；删除本目录即从 WebUI 完整拔出。
 */
import { registerPluginI18n } from "@/lib/plugin-i18n";
import type { ChannelPlugin } from "@/lib/channel-plugins";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

registerPluginI18n("channel-weixin", { zh, en });

// 配置中心分组展示名（config 命名空间，deep 合并进核心 locale）
registerPluginI18n("config", {
  zh: { sections: { "adapter/weixin": "微信" } },
  en: { sections: { "adapter/weixin": "WeChat" } },
});

export default {
  login: () => import("./components/QrLogin"),
} satisfies ChannelPlugin;
