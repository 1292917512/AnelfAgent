import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import LanguageDetector from "i18next-browser-languagedetector";

import zhCommon from "./locales/zh/common.json";
import zhNav from "./locales/zh/nav.json";
import zhDashboard from "./locales/zh/dashboard.json";
import zhChat from "./locales/zh/chat.json";
import zhStatus from "./locales/zh/status.json";
import zhModels from "./locales/zh/models.json";
import zhTools from "./locales/zh/tools.json";
import zhPersonas from "./locales/zh/personas.json";
import zhMemory from "./locales/zh/memory.json";
import zhMcp from "./locales/zh/mcp.json";
import zhChannels from "./locales/zh/channels.json";
import zhApprovals from "./locales/zh/approvals.json";
import zhThinking from "./locales/zh/thinking.json";
import zhSettings from "./locales/zh/settings.json";
import zhAppconfig from "./locales/zh/appconfig.json";
import zhTags from "./locales/zh/tags.json";
import zhHeartbeat from "./locales/zh/heartbeat.json";
import zhConfig from "./locales/zh/config.json";
import zhSkills from "./locales/zh/skills.json";
import zhWorkbench from "./locales/zh/workbench.json";

import enCommon from "./locales/en/common.json";
import enNav from "./locales/en/nav.json";
import enDashboard from "./locales/en/dashboard.json";
import enChat from "./locales/en/chat.json";
import enStatus from "./locales/en/status.json";
import enModels from "./locales/en/models.json";
import enTools from "./locales/en/tools.json";
import enPersonas from "./locales/en/personas.json";
import enMemory from "./locales/en/memory.json";
import enMcp from "./locales/en/mcp.json";
import enChannels from "./locales/en/channels.json";
import enApprovals from "./locales/en/approvals.json";
import enThinking from "./locales/en/thinking.json";
import enSettings from "./locales/en/settings.json";
import enAppconfig from "./locales/en/appconfig.json";
import enTags from "./locales/en/tags.json";
import enHeartbeat from "./locales/en/heartbeat.json";
import enConfig from "./locales/en/config.json";
import enSkills from "./locales/en/skills.json";
import enWorkbench from "./locales/en/workbench.json";

const resources = {
  zh: {
    common: zhCommon,
    nav: zhNav,
    dashboard: zhDashboard,
    chat: zhChat,
    status: zhStatus,
    models: zhModels,
    tools: zhTools,
    personas: zhPersonas,
    memory: zhMemory,
    mcp: zhMcp,
    channels: zhChannels,
    approvals: zhApprovals,
    thinking: zhThinking,
    settings: zhSettings,
    appconfig: zhAppconfig,
    tags: zhTags,
    heartbeat: zhHeartbeat,
    skills: zhSkills,
    config: zhConfig,
    workbench: zhWorkbench,
  },
  en: {
    common: enCommon,
    nav: enNav,
    dashboard: enDashboard,
    chat: enChat,
    status: enStatus,
    models: enModels,
    tools: enTools,
    personas: enPersonas,
    memory: enMemory,
    mcp: enMcp,
    channels: enChannels,
    approvals: enApprovals,
    thinking: enThinking,
    settings: enSettings,
    appconfig: enAppconfig,
    tags: enTags,
    heartbeat: enHeartbeat,
    skills: enSkills,
    config: enConfig,
    workbench: enWorkbench,
  },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: "zh",
    supportedLngs: ["zh", "en"],
    defaultNS: "common",
    ns: [
      "common",
      "nav",
      "dashboard",
      "chat",
      "status",
      "models",
      "tools",
      "personas",
      "memory",
      "mcp",
      "channels",
      "approvals",
      "thinking",
      "settings",
      "appconfig",
      "tags",
      "heartbeat",
      "skills",
      "config",
      "workbench",
    ],
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ["localStorage", "navigator"],
      caches: ["localStorage"],
    },
  });

export default i18n;
