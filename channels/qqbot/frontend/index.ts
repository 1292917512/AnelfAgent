import type { ChannelPlugin } from "@/lib/channel-plugins";
import { registerPluginI18n } from "@/lib/plugin-i18n";
import en from "./locales/en.json";
import zh from "./locales/zh.json";

registerPluginI18n("channel-qqbot", { zh, en });
registerPluginI18n("config", {
  zh: { sections: { "adapter/qqbot": "QQ 官方机器人" } },
  en: { sections: { "adapter/qqbot": "QQ Official Bot" } },
});

export default { settings: () => import("./SetupPanel") } satisfies ChannelPlugin;
