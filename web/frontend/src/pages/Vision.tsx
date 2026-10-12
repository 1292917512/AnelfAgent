import { lazy, Suspense } from "react";
import { PageSkeleton } from "@/components/common/AsyncState";
import { useRouteTab } from "@/hooks/useRouteTab";
import { useTranslation } from "react-i18next";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { Eye, LayoutGrid, ScanFace, SlidersHorizontal, Wand2 } from "lucide-react";
const VisionOverviewPanel = lazy(() => import("@/pages/vision/OverviewPanel").then((module) => ({ default: module.VisionOverviewPanel })));
const VisionCapabilitiesPanel = lazy(() => import("@/pages/vision/CapabilitiesPanel").then((module) => ({ default: module.VisionCapabilitiesPanel })));
const VisionConfigPanel = lazy(() => import("@/pages/vision/ConfigPanel").then((module) => ({ default: module.VisionConfigPanel })));
const FacePanel = lazy(() => import("@/pages/vision/face/FacePanel").then((module) => ({ default: module.FacePanel })));

type VisionTab = "overview" | "face" | "capabilities" | "config";

/** 视觉感知 — 核心能力页（/vision）：视觉源总览/监视控制/画面预览 + 人脸识别 + 生成能力链与风格预设 + 配置 */
export default function Vision() {
  const { t } = useTranslation("vision");
  const [tab, setTab] = useRouteTab<VisionTab>(["overview", "face", "capabilities", "config"], "overview");

  const TABS: TabItem<VisionTab>[] = [
    { key: "overview", label: t("tabs.overview"), icon: LayoutGrid },
    { key: "face", label: t("tabs.face"), icon: ScanFace },
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
      <Suspense fallback={<PageSkeleton />}>
      {tab === "overview" && <VisionOverviewPanel />}
      {tab === "face" && <FacePanel />}
      {tab === "capabilities" && <VisionCapabilitiesPanel />}
      {tab === "config" && <VisionConfigPanel />}
      </Suspense>
    </PageContainer>
  );
}
