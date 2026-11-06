import type { ChannelPlugin } from "@/lib/channel-plugins";
import { registerPluginI18n } from "@/lib/plugin-i18n";
import en from "./locales/en.json";
import zh from "./locales/zh.json";

registerPluginI18n("channel-github", { zh, en });

export default { settings: () => import("./Settings") } satisfies ChannelPlugin;
