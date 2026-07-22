import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Blocks, Wrench, GraduationCap, Plug, List, FileText, Server, FileJson } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { ToolSystemRulesPanel } from "@/pages/config/ToolSystemRulesPanel";
import { ToolsListPanel } from "@/pages/tools/ToolsListPanel";
import { SkillsPanel } from "@/pages/skills/SkillsPanel";
import { ServersPanel } from "@/pages/mcp/ServersPanel";
import { JsonConfigPanel } from "@/pages/mcp/JsonConfigPanel";

type CapTab = "tools" | "skills" | "mcp";
type ToolsSubTab = "list" | "rules";
type McpSubTab = "servers" | "json";

const VALID_TABS: CapTab[] = ["tools", "skills", "mcp"];

export default function Capabilities() {
  const { t } = useTranslation(["common", "nav", "tools", "mcp"]);
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as CapTab | null;
  const activeTab: CapTab = tabParam && VALID_TABS.includes(tabParam) ? tabParam : "tools";

  const [toolsSub, setToolsSub] = useState<ToolsSubTab>("list");
  const [mcpSub, setMcpSub] = useState<McpSubTab>("servers");

  const changeTab = (tab: CapTab) => {
    setSearchParams({ tab }, { replace: true });
  };

  const TABS: TabItem<CapTab>[] = [
    { key: "tools", label: t("nav:tools"), icon: Wrench },
    { key: "skills", label: t("nav:skills"), icon: GraduationCap },
    { key: "mcp", label: t("nav:mcp"), icon: Plug },
  ];

  const TOOLS_SUB_TABS: TabItem<ToolsSubTab>[] = [
    { key: "list", label: t("tools:tabs.list"), icon: List },
    { key: "rules", label: t("tools:tabs.rules"), icon: FileText },
  ];

  const MCP_SUB_TABS: TabItem<McpSubTab>[] = [
    { key: "servers", label: t("mcp:tabServers"), icon: Server },
    { key: "json", label: t("mcp:tabJson"), icon: FileJson },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Blocks size={20} className="text-accent" />}
        title={t("capabilitiesTitle")}
        subtitle={t("capabilitiesSubtitle")}
      />
      <TabBar tabs={TABS} activeTab={activeTab} onChange={changeTab} />

      {activeTab === "tools" && (
        <div className="space-y-4 mt-4">
          <TabBar tabs={TOOLS_SUB_TABS} activeTab={toolsSub} onChange={setToolsSub} />
          {toolsSub === "list" && <ToolsListPanel />}
          {toolsSub === "rules" && <ToolSystemRulesPanel />}
        </div>
      )}

      {activeTab === "skills" && (
        <div className="mt-4">
          <SkillsPanel />
        </div>
      )}

      {activeTab === "mcp" && (
        <div className="space-y-4 mt-4">
          <TabBar tabs={MCP_SUB_TABS} activeTab={mcpSub} onChange={setMcpSub} />
          {mcpSub === "servers" && <ServersPanel />}
          {mcpSub === "json" && <JsonConfigPanel />}
        </div>
      )}
    </PageContainer>
  );
}
