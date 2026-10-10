import { useMemo, Suspense } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { entitiesApi } from "@/lib/api";
import { TabBar } from "@/components/common/TabBar";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { Button } from "@/components/ui";
import { QueryError, PageSkeleton } from "@/components/common/AsyncState";
import { SectionBoundary } from "@/components/common/SectionBoundary";
import { RegisteredConfigPanel } from "./config/RegisteredConfigPanel";
import { useRouteTab } from "@/hooks/useRouteTab";
import { getEntityPanel } from "@/lib/entity-panels";
import {
  ArrowLeft,
  Settings,
  Wrench,
  LayoutDashboard,
  PanelRight,
} from "lucide-react";

type EntityTab = "overview" | "config" | "tools" | "panel";

export default function EntityDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation("entities");
  const queryClient = useQueryClient();

  const PanelComponent = useMemo(() => name ? getEntityPanel(name) : null, [name]);
  const [tab, setTab] = useRouteTab<EntityTab>(PanelComponent ? ["panel", "overview", "config", "tools"] : ["overview", "config", "tools"], PanelComponent ? "panel" : "overview");

  const query = useQuery({
    queryKey: ["entity-detail", name],
    queryFn: () => entitiesApi.detail(name!).then((r) => r.data),
    enabled: !!name,
    throwOnError: false,
  });

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => entitiesApi.toggle(name!, enabled),
    onSuccess: async () => { await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["entity-detail", name] }),
      queryClient.invalidateQueries({ queryKey: ["tools-grouped"] }),
      queryClient.invalidateQueries({ queryKey: ["entities"] }),
    ]); },
  });

  const entity = query.data;
  if (query.isError && !entity) return <div className="p-5"><QueryError error={query.error} retry={() => void query.refetch()} /></div>;
  if (!entity) return <div className="p-5"><PageSkeleton /></div>;

  const manifest = entity.manifest;
  const displayName = manifest?.display_name || entity.group || entity.name;
  const tabs = [
    ...(PanelComponent ? [{ key: "panel" as EntityTab, label: t("tabs.panel"), icon: PanelRight }] : []),
    { key: "overview" as EntityTab, label: t("tabs.overview"), icon: LayoutDashboard },
    { key: "config" as EntityTab, label: t("tabs.config"), icon: Settings },
    { key: "tools" as EntityTab, label: t("tabs.tools"), icon: Wrench },
  ];

  return (
    <div className="h-full flex flex-col">
      {/* 头部 */}
      <div className="px-4 md:px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/tools")}
            aria-label={t("backToTools")}
            className="grid size-10 shrink-0 place-items-center rounded-lg text-muted hover:text-foreground hover:bg-hover transition-colors"
          >
            <ArrowLeft size={16} />
          </button>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-semibold text-heading truncate">{displayName}</h1>
              {manifest?.version && (
                <span className="text-[10px] font-mono text-muted bg-elevated px-1.5 py-0.5 rounded">
                  v{manifest.version}
                </span>
              )}
            </div>
            <p className="text-sm leading-relaxed text-muted mt-1">
              {manifest?.description || entity.description}
            </p>
          </div>
          <Button size="sm" loading={toggleMutation.isPending} aria-pressed={entity.enabled}
            onClick={() => toggleMutation.mutate(!entity.enabled)}>
            <StatusDot status={entity.enabled ? "ok" : "offline"} />
            {entity.enabled ? t("enabled") : t("disabled")}
          </Button>
        </div>
      </div>

      {/* TabBar */}
      <div className="px-4 md:px-6 pb-3 border-b border-border">
        <TabBar tabs={tabs} activeTab={tab} onChange={setTab} />
      </div>

      {/* 内容区 */}
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {tab === "overview" && (
          <div className="space-y-4 max-w-2xl">
            <Card title={t("overview.basicInfo")}>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div><span className="text-muted">{t("overview.name")}</span><p className="font-mono text-foreground mt-0.5">{entity.name}</p></div>
                <div><span className="text-muted">{t("overview.group")}</span><p className="font-mono text-foreground mt-0.5">{entity.group}</p></div>
                <div><span className="text-muted">{t("overview.type")}</span><p className="text-foreground mt-0.5">{entity.type}</p></div>
                <div><span className="text-muted">{t("overview.source")}</span><p className="text-foreground mt-0.5">{entity.source}</p></div>
                <div><span className="text-muted">{t("overview.tools")}</span><p className="text-foreground mt-0.5">{entity.tools.length}</p></div>
                <div><span className="text-muted">{t("overview.apis")}</span><p className="text-foreground mt-0.5">{entity.apis.length}</p></div>
              </div>
            </Card>
            {entity.providers.length > 0 && (
              <Card title={t("overview.providers")}>
                <div className="space-y-2">
                  {entity.providers.map((p) => (
                    <div key={p.name} className="flex items-center gap-2 text-xs">
                      <span className="w-1.5 h-1.5 rounded-full bg-ok" />
                      <span className="font-mono text-foreground">{p.name}</span>
                      <span className="text-muted">priority={p.priority} · max_tokens={p.max_tokens}</span>
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
        )}

        {tab === "config" && (
          <div className="mx-auto max-w-4xl">
            <RegisteredConfigPanel key={name} group={entity.config_group} />
          </div>
        )}

        {tab === "tools" && (
          <div className="max-w-2xl space-y-2">
            {entity.tools.length === 0 ? (
              <p className="text-sm text-muted py-4">{t("tools.empty")}</p>
            ) : (
              entity.tools.map((tool) => (
                <div key={tool.name} className="flex items-center gap-3 py-2 px-3 rounded-md bg-elevated border border-border">
                  <StatusDot status={tool.enabled ? "ok" : "offline"} />
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-mono text-foreground">{tool.name}</span>
                    {tool.description && <p className="text-xs leading-relaxed text-muted">{tool.description}</p>}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === "panel" && PanelComponent && (
          <SectionBoundary><Suspense fallback={<div className="text-sm text-muted py-4">{t("loading")}</div>}>
            {/* eslint-disable-next-line react-hooks/static-components -- 面板按实体名动态解析：注册表模块加载时预建（entity-panels.ts），非渲染期创建组件 */}
            <PanelComponent />
          </Suspense></SectionBoundary>
        )}
      </div>
    </div>
  );
}
