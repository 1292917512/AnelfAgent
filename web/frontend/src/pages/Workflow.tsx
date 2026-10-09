import { useTranslation } from "react-i18next";
import { Waypoints } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { RunsPanel } from "@/pages/workflow/RunsPanel";

/** 工作流页：运行列表 + 详情时间线 + 规格启动。 */
export default function Workflow() {
  const { t } = useTranslation("workflow");

  return (
    <PageContainer>
      <PageHeader
        icon={<Waypoints size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <RunsPanel />
    </PageContainer>
  );
}
