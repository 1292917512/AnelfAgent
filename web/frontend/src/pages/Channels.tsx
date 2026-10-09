import { useRouteTab } from "@/hooks/useRouteTab";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageIntro } from "@/components/common/PageContainer";
import { FlaskConical } from "lucide-react";
import { ChannelsPanel } from "@/pages/channels/ChannelsPanel";
import { ChannelTestPanel } from "@/pages/channels/ChannelTestPanel";
import { ChannelToolsDrawer, type ChannelToolsTarget } from "@/pages/channels/ChannelToolsDrawer";

type ChannelTab = "channels" | "test";

export default function Channels() {
  const { t } = useTranslation("channels");
  const [activeTab, setActiveTab] = useRouteTab<ChannelTab>(["channels", "test"], "channels");
  const [toolsChannel, setToolsChannel] = useState<ChannelToolsTarget | null>(null);
  const [testChannelKey, setTestChannelKey] = useState<string>("");

  const tabs: TabItem<ChannelTab>[] = [
    { key: "channels", label: t("tabs.channels") },
    { key: "test", label: t("tabs.test"), icon: FlaskConical },
  ];

  return (
    <PageContainer>
      <PageIntro />
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "test" ? (
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
