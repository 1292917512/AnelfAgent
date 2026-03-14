import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Plug } from "lucide-react";
import { ChannelsPanel } from "@/pages/channels/ChannelsPanel";
import { NoneBotPanel } from "@/pages/channels/NoneBotPanel";

type ChannelTab = "channels" | "nonebot";

export default function Channels() {
  const { t } = useTranslation("channels");
  const [activeTab, setActiveTab] = useState<ChannelTab>("channels");

  const tabs: TabItem<ChannelTab>[] = [
    { key: "channels", label: t("tabs.channels") },
    { key: "nonebot", label: t("tabs.nonebot"), icon: Plug },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      <TabBar tabs={tabs} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "nonebot" ? (
        <NoneBotPanel />
      ) : (
        <ChannelsPanel />
      )}
    </div>
  );
}
