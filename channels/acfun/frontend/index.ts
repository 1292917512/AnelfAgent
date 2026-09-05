/**
 * AcFun 频道前端插件 — 登录（扫码/账密+验证码）+ 直播模式实时面板。
 *
 * 插件契约（src/lib/channel-plugins.ts）：本文件为轻量 eager 清单，
 * i18n 自注册 + 组件懒加载；删除本目录即从 WebUI 完整拔出。
 */
import { registerPluginI18n } from "@/lib/plugin-i18n";
import type { ChannelPlugin } from "@/lib/channel-plugins";
import zh from "./locales/zh.json";
import en from "./locales/en.json";

registerPluginI18n("channel-acfun", { zh, en });

// 配置中心分组展示名（config 命名空间，deep 合并进核心 locale）
registerPluginI18n("config", {
  zh: { sections: { "adapter/acfun": "AcFun" } },
  en: { sections: { "adapter/acfun": "AcFun" } },
});

export default {
  login: () => import("./components/Login"),
  panel: () => import("./components/LivePanel"),
} satisfies ChannelPlugin;
