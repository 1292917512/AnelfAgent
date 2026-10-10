/** 运维管理：服务操作、构建记录与崩溃诊断。 */
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Card } from "@/components/common/Card";
import { devopsApi } from "./panels/api";
import ServiceControls from "./panels/ServiceControls";
import { SectionBoundary } from "@/components/common/SectionBoundary";

export default function DevopsPanel() {
  const { t } = useTranslation("devops");
  return <div className="max-w-2xl space-y-4">
    <Card title={t("serviceControl")} subtitle={t("projectUpdateDesc")}><ServiceControls full /></Card>
    <SectionBoundary><Diagnostics /></SectionBoundary>
  </div>;
}

function Diagnostics() {
  const { t } = useTranslation("devops");
  const { data: buildState } = useQuery({ queryKey: ["devops-build-state"], queryFn: () => devopsApi.buildState().then((r) => r.data), refetchInterval: 15000 });
  const { data: crashInfo } = useQuery({ queryKey: ["devops-crash-info"], queryFn: () => devopsApi.crashInfo().then((r) => r.data), refetchInterval: 60000 });
  return <div className="max-w-2xl space-y-4">
    {buildState?.last && <p className="text-xs text-muted">{t("lastBuild")}: {buildState.last.finished_at} · {buildState.last.duration}s · <span className={buildState.last.ok ? "text-ok" : "text-danger"}>{buildState.last.ok ? "OK" : t("buildFailed")}</span></p>}
    {crashInfo?.has_crash && crashInfo.summary && <Card title={t("crashInfo")} subtitle={t("crashInfoDesc")}>
      <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-elevated p-3 font-mono text-xs">{crashInfo.summary}</pre>
      <p className="mt-2 text-xs text-muted">{t("crashAutoRestartHint")}</p>
    </Card>}
  </div>;
}
