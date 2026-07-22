import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plug, Server, FileJson } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { ServersPanel } from "@/pages/mcp/ServersPanel";
import { JsonConfigPanel } from "@/pages/mcp/JsonConfigPanel";

type McpTab = "servers" | "json";

export default function Mcp() {
  const { t } = useTranslation(["mcp"]);
  const [tab, setTab] = useState<McpTab>("servers");

  const TABS: TabItem<McpTab>[] = [
    { key: "servers", label: t("mcp:tabServers"), icon: Server },
    { key: "json", label: t("mcp:tabJson"), icon: FileJson },
  ];

  return (
    <PageContainer wide>
      <PageHeader
        icon={<Plug size={20} className="text-accent" />}
        title={t("mcp:title")}
        subtitle={t("mcp:subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      <div className="mt-4">
        {tab === "servers" && <ServersPanel />}
        {tab === "json" && <JsonConfigPanel />}
      </div>
    </PageContainer>
  );
}
