import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Globe, SlidersHorizontal, Search } from "lucide-react";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { RetrievalProvidersPanel } from "@/pages/retrieval/ProvidersPanel";
import { RetrievalSettingsPanel } from "@/pages/retrieval/SettingsPanel";

type RetrievalTab = "providers" | "settings";

/** 检索 — 核心能力页（/retrieval）：能力 × 提供者矩阵管理 + 抓取设置 */
export default function Retrieval() {
  const { t } = useTranslation("retrieval");
  const [tab, setTab] = useState<RetrievalTab>("providers");

  const TABS: TabItem<RetrievalTab>[] = [
    { key: "providers", label: t("tabs.providers"), icon: Globe },
    { key: "settings", label: t("tabs.settings"), icon: SlidersHorizontal },
  ];

  return (
    <PageContainer>
      <PageHeader
        icon={<Search size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "providers" && <RetrievalProvidersPanel />}
      {tab === "settings" && <RetrievalSettingsPanel />}
    </PageContainer>
  );
}
