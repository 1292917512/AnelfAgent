import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { systemApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { LoadingBlock } from "@/components/ui";
import { InfoRow } from "./shared";

/** 系统信息面板：OS / CPU / 内存 / Python 环境 / 开发工具 */
export function SystemPanel() {
  const { t } = useTranslation("settings");
  const { data, isLoading } = useQuery({
    queryKey: ["systemInfo"],
    queryFn: () => systemApi.info().then((r) => r.data),
  });

  if (isLoading) return <Card><LoadingBlock /></Card>;

  const sys = data?.system;
  const py = data?.python;
  const tools: Array<{ name: string; installed: boolean; version?: string }> = data?.tools ?? [];
  const installed = tools.filter((tool) => tool.installed);
  const missing = tools.filter((tool) => !tool.installed);

  return (
    <div className="space-y-4">
      {sys && (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <StatCard label={t("os")} value={`${sys.os} ${sys.os_release}`} />
            <StatCard label={t("architecture")} value={sys.architecture} />
            {sys.cpu_logical && <StatCard label={t("cpu")} value={`${sys.cpu_physical}C / ${sys.cpu_logical}T`} />}
            {sys.memory_total_gb && <StatCard label={t("memoryLabel")} value={`${sys.memory_used_gb} / ${sys.memory_total_gb} GB`} />}
          </div>
          <Card title={t("systemDetails")}>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <InfoRow label={t("processor")} value={sys.processor} />
              <InfoRow label={t("user")} value={sys.user} />
              <InfoRow label={t("homeDir")} value={sys.home} />
              <InfoRow label="Shell" value={sys.shell} />
              {sys.disk_total_gb ? (
                <InfoRow label={t("disk")} value={`${sys.disk_used_gb} / ${sys.disk_total_gb} GB`} />
              ) : null}
            </div>
          </Card>
        </>
      )}

      {py && (
        <Card title={t("pythonEnv")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <InfoRow label={t("version")} value={py.version} />
            <InfoRow label={t("implementation")} value={py.implementation} />
            <InfoRow label={t("path")} value={py.executable} />
            <InfoRow label={t("virtualEnv")} value={py.in_venv ? py.venv_path : t("systemEnv")} />
          </div>
        </Card>
      )}

      <Card title={t("devTools")} subtitle={`${installed.length} ${t("installed")} / ${missing.length} ${t("notInstalled")}`}>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {tools.map((tool) => (
            <div key={tool.name} className="flex items-center gap-2 p-2.5 rounded-md bg-elevated border border-border">
              {tool.installed
                ? <Check size={14} className="text-ok flex-shrink-0" />
                : <X size={14} className="text-muted flex-shrink-0" />}
              <div className="min-w-0">
                <p className="text-sm font-medium text-heading">{tool.name}</p>
                {tool.version && <p className="text-[11px] text-muted truncate">{tool.version}</p>}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
