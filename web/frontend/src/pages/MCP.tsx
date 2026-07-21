import { useState } from "react";
import { useTranslation } from "react-i18next";
import { FileJson, Plug, Server } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { ServersPanel } from "@/pages/mcp/ServersPanel";
import { JsonConfigPanel } from "@/pages/mcp/JsonConfigPanel";

type McpTab = "servers" | "json";

export default function MCP() {
  const { t } = useTranslation("mcp");
  const [tab, setTab] = useState<McpTab>("servers");

  const TABS: TabItem<McpTab>[] = [
    { key: "servers", label: t("tabServers"), icon: Server },
    { key: "json", label: t("tabJson"), icon: FileJson },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Plug size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "servers" && <ServersPanel />}
      {tab === "json" && <JsonConfigPanel />}
    </PageContainer>
  );
}
