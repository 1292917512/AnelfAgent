/**
 * SillyTavern 酒馆实体面板 — 独立管理界面，随实体目录整体插拔。
 * i18n 自注册 + 六个标签页：运行状态 / 内嵌酒馆网页 / 角色卡 / 模型设置 / 仓库更新 / 实体配置。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Activity,
  Castle,
  GitBranch,
  MessageSquare,
  MonitorPlay,
  Settings2,
  SlidersHorizontal,
  Users,
} from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { registerPluginI18n } from "@/lib/plugin-i18n";
import { OverviewPanel } from "./sillytavern/OverviewPanel";
import { WebPanel } from "./sillytavern/WebPanel";
import { ChatPanel } from "./sillytavern/ChatPanel";
import { CharactersPanel } from "./sillytavern/CharactersPanel";
import { ModelPanel } from "./sillytavern/ModelPanel";
import { GitPanel } from "./sillytavern/GitPanel";
import { ConfigPanel } from "./sillytavern/ConfigPanel";
import zh from "./sillytavern/locales/zh.json";
import en from "./sillytavern/locales/en.json";

registerPluginI18n("sillytavern", { zh, en });

type StTab = "overview" | "web" | "chat" | "characters" | "model" | "git" | "config";

const TABS: TabItem<StTab>[] = [
  { key: "overview", label: "sillytavern:tabOverview", icon: Activity },
  { key: "web", label: "sillytavern:tabWeb", icon: MonitorPlay },
  { key: "chat", label: "sillytavern:tabChat", icon: MessageSquare },
  { key: "characters", label: "sillytavern:tabCharacters", icon: Users },
  { key: "model", label: "sillytavern:tabModel", icon: SlidersHorizontal },
  { key: "git", label: "sillytavern:tabGit", icon: GitBranch },
  { key: "config", label: "sillytavern:tabConfig", icon: Settings2 },
];

export default function SillyTavernPanel() {
  const { t } = useTranslation(["sillytavern"]);
  const [tab, setTab] = useState<StTab>("overview");

  const resolvedTabs: TabItem<StTab>[] = TABS.map((item) => ({
    ...item,
    label: t(item.label),
  }));

  return (
    <PageContainer>
      <PageHeader
        icon={<Castle size={20} className="text-accent" />}
        title={t("sillytavern:title")}
        subtitle={t("sillytavern:subtitle")}
      />
      <TabBar tabs={resolvedTabs} activeTab={tab} onChange={setTab} />
      <div className="mt-4">
        {tab === "overview" && <OverviewPanel />}
        {tab === "web" && <WebPanel />}
        {tab === "chat" && <ChatPanel />}
        {tab === "characters" && <CharactersPanel />}
        {tab === "model" && <ModelPanel />}
        {tab === "git" && <GitPanel />}
        {tab === "config" && <ConfigPanel />}
      </div>
    </PageContainer>
  );
}
