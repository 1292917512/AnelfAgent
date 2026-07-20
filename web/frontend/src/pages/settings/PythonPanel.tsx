import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { systemApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { InfoRow } from "./shared";

/** Python 环境面板：状态 / pip 镜像 / 已安装包 */
export function PythonPanel() {
  const { t } = useTranslation("settings");
  const { data: status } = useQuery({
    queryKey: ["pythonStatus"],
    queryFn: () => systemApi.python().then((r) => r.data),
  });
  const { data: packages } = useQuery({
    queryKey: ["pythonPackages"],
    queryFn: () => systemApi.pythonPackages().then((r) => r.data),
  });
  const { data: mirror } = useQuery({
    queryKey: ["pipMirror"],
    queryFn: () => systemApi.pipMirror().then((r) => r.data),
  });

  return (
    <div className="space-y-4">
      {status && (
        <Card title={t("pythonEnvStatus")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(status as Record<string, unknown>)
              .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
              .map(([k, v]) => (
                <InfoRow key={k} label={<span className="font-mono">{k}</span>} value={String(v)} mono={false} />
              ))}
          </div>
        </Card>
      )}

      {mirror && (
        <Card title={t("pipMirror")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(mirror as Record<string, unknown>)
              .filter(([, v]) => typeof v === "string")
              .map(([k, v]) => (
                <InfoRow key={k} label={k} value={String(v)} />
              ))}
          </div>
        </Card>
      )}

      <Card title={t("installedPackages")} subtitle={t("nPackages", { count: (packages as unknown[])?.length ?? 0 })}>
        <div className="max-h-80 overflow-y-auto space-y-1">
          {(packages as Array<{ name: string; version: string }> ?? []).map((p) => (
            <div key={p.name} className="flex items-center justify-between py-1.5 px-3 rounded-sm hover:bg-hover transition-colors">
              <span className="text-sm text-foreground">{p.name}</span>
              <span className="text-xs text-muted font-mono">{p.version}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
