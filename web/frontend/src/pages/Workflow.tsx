import { useTranslation } from "react-i18next";
import { Waypoints } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { TabBar } from "@/components/common/TabBar";
import { DefinitionsPanel } from "@/pages/workflow/DefinitionsPanel";
import { RunsPanel } from "@/pages/workflow/RunsPanel";

/** 工作流页：运行列表 + 详情时间线 + 规格启动。 */
export default function Workflow() {
  const { t } = useTranslation("workflow");
  const [params, setParams] = useSearchParams();
  const tab = params.has("run") || params.get("tab") === "runs" ? "runs" : "definitions";

  return (
    <PageContainer>
      <PageHeader
        icon={<Waypoints size={20} className="text-accent" />}
        title={t("title")}
        subtitle={t("subtitle")}
      />
      <TabBar tabs={[{ key: "definitions", label: t("definitions.title") }, { key: "runs", label: t("list.title") }]}
        activeTab={tab} onChange={(next) => setParams((current) => {
          const result = new URLSearchParams(current); result.set("tab", next); result.delete("run"); return result;
        })} />
      {tab === "definitions" ? <DefinitionsPanel onStarted={(run) => setParams({ tab: "runs", run: run.run_id })} /> : <RunsPanel />}
    </PageContainer>
  );
}
