import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Plug, Shield } from "lucide-react";
import { ChannelsPanel } from "@/pages/channels/ChannelsPanel";
import { NoneBotPanel } from "@/pages/channels/NoneBotPanel";
import { ApprovalsPanel } from "@/pages/channels/ApprovalsPanel";

type ChannelTab = "channels" | "nonebot" | "approvals";

export default function Channels() {
  const { t } = useTranslation("channels");
  const [activeTab, setActiveTab] = useState<ChannelTab>("channels");

  const tabs: TabItem<ChannelTab>[] = [
    { key: "channels", label: t("tabs.channels") },
    { key: "nonebot", label: t("tabs.nonebot"), icon: Plug },
    { key: "approvals", label: t("tabs.approvals"), icon: Shield },
  ];

  return (
    <PageContainer wide>
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "nonebot" ? (
        <NoneBotPanel />
      ) : activeTab === "approvals" ? (
        <ApprovalsPanel />
      ) : (
        <ChannelsPanel />
      )}
    </PageContainer>
  );
}
