/**
 * HTTP API 频道前端插件 — 仅自注册配置中心的分组展示名（config 命名空间）。
 *
 * 插件契约（src/lib/channel-plugins.ts）：本文件为轻量 eager 清单，
 * i18n 自注册 + 组件懒加载；删除本目录即从 WebUI 完整拔出。
 */
import { registerPluginI18n } from "@/lib/plugin-i18n";
import type { ChannelPlugin } from "@/lib/channel-plugins";

registerPluginI18n("config", {
  zh: { sections: { "adapter/http_api": "HTTP API" } },
  en: { sections: { "adapter/http_api": "HTTP API" } },
});

export default {} satisfies ChannelPlugin;
