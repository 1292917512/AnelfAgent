import { registerPluginI18n } from "@/lib/plugin-i18n";
import zh from "./dify/locales/zh.json";
import en from "./dify/locales/en.json";

registerPluginI18n("dify", { zh, en });

/**
 * Dify 平台实体面板 — 连接 / 应用与工作流 / 运行调试 / 模型供应商 / MCP 桥接。
 *
 * 经 scripts/module-links.mjs 软链到前端 panels 目录，
 * Vite import.meta.glob 自动发现并懒加载（实体详情页 panel tab，不占侧边栏）。
 * 与 AI 工具共用同一实现（/api/entity/dify → entities/dify/service.py）。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { AppWindow, Cpu, Link2, Play, Share2 } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { StatusTab } from "./dify/StatusTab";
import { AppsTab } from "./dify/AppsTab";
import { RunTab } from "./dify/RunTab";
import { ModelsTab } from "./dify/ModelsTab";
import { McpTab } from "./dify/McpTab";

type DifyTab = "status" | "apps" | "run" | "models" | "mcp";

export default function DifyPanel() {
  const { t } = useTranslation("dify");
  const [tab, setTab] = useState<DifyTab>("status");

  const TABS: TabItem<DifyTab>[] = [
    { key: "status", label: t("tabs.status"), icon: Link2 },
    { key: "apps", label: t("tabs.apps"), icon: AppWindow },
    { key: "run", label: t("tabs.run"), icon: Play },
    { key: "models", label: t("tabs.models"), icon: Cpu },
    { key: "mcp", label: t("tabs.mcp"), icon: Share2 },
  ];

  return (
    <div className="space-y-4">
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "status" && <StatusTab />}
      {tab === "apps" && <AppsTab />}
      {tab === "run" && <RunTab />}
      {tab === "models" && <ModelsTab />}
      {tab === "mcp" && <McpTab />}
    </div>
  );
}
