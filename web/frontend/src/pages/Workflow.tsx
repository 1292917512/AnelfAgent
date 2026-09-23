import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Waypoints } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { RunsPanel } from "@/pages/workflow/RunsPanel";
import type { WorkflowRun } from "@/lib/api";

/** 工作流页：运行列表 + 详情时间线 + 规格启动。 */
export default function Workflow() {
  const { t } = useTranslation("workflow");
  const [selected, setSelected] = useState<WorkflowRun | null>(null);

  return (
    <PageContainer>
      <PageHeader
        icon={<Waypoints size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <RunsPanel selected={selected} onSelect={setSelected} />
    </PageContainer>
  );
}
