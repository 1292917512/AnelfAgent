import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge } from "@/components/ui/Badge";

type DotStatus = "ok" | "warn" | "danger" | "offline";

const NODE_STATE_DOT: Record<string, DotStatus> = {
  success: "ok",
  skipped: "warn",
  failure: "danger",
  crashed: "danger",
  upstream_failed: "offline",
};

/** Lifecycle 宿主服务清单 + 启动流程时间线。 */
export function ServicesPanel() {
  const { t } = useTranslation("dashboard");
  const { data: services } = useQuery({
    queryKey: ["services"],
    queryFn: () => statusApi.services().then((r) => r.data.services),
    refetchInterval: 10000,
  });
  const { data: timeline } = useQuery({
    queryKey: ["startup"],
    queryFn: () => statusApi.startup().then((r) => r.data.timeline),
  });

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <Card title={t("services.title")} subtitle={t("services.subtitle")}>
        {services && services.length > 0 ? (
          <div className="rounded-md border border-border overflow-auto max-h-[320px]">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-panel">
                <tr className="text-left text-muted">
                  <th className="py-2 px-3 font-medium">#</th>
                  <th className="py-2 px-3 font-medium">{t("services.name")}</th>
                  <th className="py-2 px-3 font-medium">{t("services.type")}</th>
                  <th className="py-2 px-3 font-medium">{t("services.hooks")}</th>
                </tr>
              </thead>
              <tbody>
                {services.map((s) => (
                  <tr key={s.name} className="border-b border-border/50 hover:bg-hover/50">
                    <td className="py-1.5 px-3 text-muted">{s.order}</td>
                    <td className="py-1.5 px-3 text-foreground font-mono">{s.name}</td>
                    <td className="py-1.5 px-3 text-muted">{s.instance_type}</td>
                    <td className="py-1.5 px-3">
                      <span className="flex gap-1">
                        {s.has_on_start && <Badge variant="accent">start</Badge>}
                        {s.has_cleanup && <Badge variant="ok">stop</Badge>}
                        {s.has_on_tick && <Badge variant="info">tick</Badge>}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <p className="text-muted text-sm">{t("services.empty")}</p>}
      </Card>

      <Card title={t("services.startupTitle")} subtitle={t("services.startupSubtitle")}>
        {timeline && timeline.length > 0 ? (
          <div className="space-y-1 max-h-[320px] overflow-y-auto">
            {timeline.map((n, i) => (
              <div key={`${n.name}-${i}`} className="flex items-center gap-2 py-1.5 px-3 rounded-sm bg-elevated border border-border">
                <StatusDot status={NODE_STATE_DOT[n.state] ?? "offline"} />
                <span className="text-xs font-mono text-foreground flex-1 truncate" title={n.error ?? n.name}>{n.name}</span>
                {n.attempts > 1 && <Badge variant="warn">×{n.attempts}</Badge>}
                <span className="text-[10px] text-muted">{t(`services.states.${n.state}`, { defaultValue: n.state })}</span>
                <span className="text-[10px] text-muted w-16 text-right">{n.duration.toFixed(2)}s</span>
              </div>
            ))}
          </div>
        ) : <p className="text-muted text-sm">{t("services.startupEmpty")}</p>}
      </Card>
    </div>
  );
}
