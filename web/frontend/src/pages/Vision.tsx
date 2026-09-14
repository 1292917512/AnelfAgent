import { useState } from "react";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Eye, LayoutGrid, SlidersHorizontal, Wand2 } from "lucide-react";
import { VisionOverviewPanel } from "@/pages/vision/OverviewPanel";
import { VisionCapabilitiesPanel } from "@/pages/vision/CapabilitiesPanel";
import { VisionConfigPanel } from "@/pages/vision/ConfigPanel";

type VisionTab = "overview" | "capabilities" | "config";

/** 视觉感知 — 核心能力页（/vision）：视觉源总览/监视控制/画面预览 + 生成能力链与风格预设 + 配置 */
export default function Vision() {
  const { t } = useTranslation("vision");
  const [tab, setTab] = useState<VisionTab>("overview");

  const TABS: TabItem<VisionTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: LayoutGrid },
    { key: "capabilities", label: t("tabs.capabilities"), icon: Wand2 },
    { key: "config", label: t("tabs.config"), icon: SlidersHorizontal },
  ];

  return (
    <PageContainer>
      <PageHeader
        icon={<Eye size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={TABS} activeTab={tab} onChange={setTab} />
      {tab === "overview" && <VisionOverviewPanel />}
      {tab === "capabilities" && <VisionCapabilitiesPanel />}
      {tab === "config" && <VisionConfigPanel />}
    </PageContainer>
  );
}
