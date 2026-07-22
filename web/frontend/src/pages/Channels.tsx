import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer } from "@/components/common/PageContainer";
import { Plug, Shield, FlaskConical } from "lucide-react";
import { ChannelsPanel } from "@/pages/channels/ChannelsPanel";
import { NoneBotPanel } from "@/pages/channels/NoneBotPanel";
import { ApprovalsPanel } from "@/pages/channels/ApprovalsPanel";
import { ChannelTestPanel } from "@/pages/channels/ChannelTestPanel";
import { ChannelToolsDrawer, type ChannelToolsTarget } from "@/pages/channels/ChannelToolsDrawer";

type ChannelTab = "channels" | "nonebot" | "approvals" | "test";

export default function Channels() {
  const { t } = useTranslation("channels");
  const [activeTab, setActiveTab] = useState<ChannelTab>("channels");
  const [toolsChannel, setToolsChannel] = useState<ChannelToolsTarget | null>(null);
  const [testChannelKey, setTestChannelKey] = useState<string>("");

  const tabs: TabItem<ChannelTab>[] = [
    { key: "channels", label: t("tabs.channels") },
    { key: "test", label: t("tabs.test"), icon: FlaskConical },
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
      ) : activeTab === "test" ? (
        <ChannelTestPanel initialKey={testChannelKey} />
      ) : (
        <ChannelsPanel onOpenTools={setToolsChannel} />
      )}

      <ChannelToolsDrawer
        channel={toolsChannel}
        onClose={() => setToolsChannel(null)}
        onGoTest={(key) => {
          setToolsChannel(null);
          setTestChannelKey(key);
          setActiveTab("test");
        }}
      />
    </PageContainer>
  );
}
