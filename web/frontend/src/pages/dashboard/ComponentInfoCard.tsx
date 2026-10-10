import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { statusApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { QueryError } from "@/components/common/AsyncState";
import { Badge } from "@/components/ui/Badge";
import { StatusDot } from "@/components/common/StatusDot";
import { Cpu, Database, Wrench, UserCircle, Brain, AlertTriangle } from "lucide-react";

/** 运行时模型、存储与工具注册概况。 */
export function ComponentInfoCard() {
  const { t } = useTranslation(["dashboard", "common"]);
  const { data, error, refetch } = useQuery({
    queryKey: ["components"],
    queryFn: () => statusApi.components().then((r) => r.data),
    refetchInterval: 10000,
  });

  const c = data?.structured;
  if (error) return <Card title={t("componentInfo")}><QueryError compact error={error} retry={() => void refetch()} /></Card>;

  if (!c) {
    return (
      <Card title={t("componentInfo")} subtitle={t("componentSubtitle")}>
        <p className="text-muted text-sm">{t("loading", { ns: "common" })}</p>
      </Card>
    );
  }

  if (!c.ready) {
    return (
      <Card title={t("componentInfo")} subtitle={t("componentSubtitle")}>
        <div className="flex items-center gap-2 text-sm text-warn py-2">
          <AlertTriangle size={16} />
          {c.error ?? t("comp.notReady")}
        </div>
      </Card>
    );
  }

  const tools = c.tools ?? { enabled: 0, total: 0, by_source: {} };
  const toolPct = tools.total > 0 ? Math.round((tools.enabled / tools.total) * 100) : 0;

  return (
    <Card title={t("componentInfo")} subtitle={t("componentSubtitle")}>
      <div className="overview-components divide-y divide-border">
        <div className="flex items-center gap-3 py-2.5">
          <Cpu size={16} className="text-accent shrink-0" />
          <span className="text-sm text-muted w-20 shrink-0">{t("comp.llm")}</span>
          <span className="text-sm text-heading font-medium truncate">{c.llm?.impl}</span>
          {c.llm?.model && (
            <Badge variant="accent" className="font-mono normal-case tracking-normal">
              {c.llm.model}
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-3 py-2.5">
          <Database size={16} className="text-accent shrink-0" />
          <span className="text-sm text-muted w-20 shrink-0">{t("comp.storage")}</span>
          <span className="text-sm text-heading font-medium truncate">{c.storage?.impl}</span>
          <span className="text-xs text-muted truncate">SQLite: {c.storage?.sqlite}</span>
        </div>

        <div className="flex items-center gap-3 py-2.5">
          <Wrench size={16} className="text-accent shrink-0" />
          <span className="text-sm text-muted w-20 shrink-0">{t("comp.tools")}</span>
          <div className="flex-1 min-w-0 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-sm text-heading font-medium">
                {t("comp.toolsEnabled", { enabled: tools.enabled, total: tools.total })}
              </span>
              <div className="flex-1 max-w-32 h-1.5 rounded-full bg-secondary overflow-hidden">
                <div className="h-full rounded-full bg-ok" style={{ width: `${toolPct}%` }} />
              </div>
            </div>
            {Object.keys(tools.by_source).length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(tools.by_source).map(([src, cnt]) => (
                  <Badge key={src} variant="neutral" className="font-mono normal-case tracking-normal">
                    {src} · {cnt}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center gap-3 py-2.5">
          <UserCircle size={16} className="text-accent shrink-0" />
          <span className="text-sm text-muted w-20 shrink-0">{t("comp.personaPrompts")}</span>
          <span className="text-sm text-heading font-medium">{t("comp.items", { count: c.persona_prompts ?? 0 })}</span>
        </div>

        <div className="flex items-center gap-3 py-2.5">
          <Brain size={16} className="text-accent shrink-0" />
          <span className="text-sm text-muted w-20 shrink-0">{t("comp.stm")}</span>
          <span className="text-sm text-heading font-medium">{t("comp.items", { count: c.short_term_memory ?? 0 })}</span>
          <StatusDot status="ok" />
        </div>
      </div>
    </Card>
  );
}
