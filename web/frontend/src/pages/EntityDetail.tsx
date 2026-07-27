import { useEffect, useState, Suspense, type ComponentType, type LazyExoticComponent } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { entitiesApi } from "@/lib/api";
import { TabBar } from "@/components/common/TabBar";
import { Card } from "@/components/common/Card";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import type { EntityDetail as EntityDetailType } from "@/lib/types";
import { getEntityPanel } from "@/lib/entity-panels";
import {
  ArrowLeft,
  Settings,
  Wrench,
  LayoutDashboard,
  PanelRight,
  ToggleLeft,
  ToggleRight,
  Save,
} from "lucide-react";

type EntityTab = "overview" | "config" | "tools" | "panel";

function ConfigForm({ entity }: { entity: EntityDetailType }) {
  const { t } = useTranslation("entities");
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    const initial: Record<string, unknown> = {};
    for (const item of entity.config_items) {
      initial[item.key] = entity.configs[item.key] ?? item.default_value;
    }
    setDraft(initial);
    setDirty(false);
  }, [entity]);

  const saveMutation = useMutation({
    mutationFn: () => entitiesApi.updateConfigBatch(entity.name, draft),
    onSuccess: () => {
      setDirty(false);
      queryClient.invalidateQueries({ queryKey: ["entity-detail", entity.name] });
    },
  });

  if (!entity.config_items.length) {
    return <p className="text-sm text-muted py-4">{t("config.empty")}</p>;
  }

  return (
    <div className="space-y-4">
      {entity.config_items.map((item) => (
        <div key={item.key} className="flex items-center gap-3">
          <div className="flex-1 min-w-0">
            <label className="text-xs font-medium text-foreground block">{item.key}</label>
            <p className="text-[10px] text-muted">{item.description}</p>
          </div>
          <div className="w-56">
            {item.value_type === "bool" ? (
              <button
                onClick={() => { setDraft((d) => ({ ...d, [item.key]: !d[item.key] })); setDirty(true); }}
                className="text-accent"
              >
                {draft[item.key] ? <ToggleRight size={24} /> : <ToggleLeft size={24} className="text-muted" />}
              </button>
            ) : item.enum_options?.length ? (
              <select
                value={String(draft[item.key] ?? "")}
                onChange={(e) => { setDraft((d) => ({ ...d, [item.key]: e.target.value })); setDirty(true); }}
                className="w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground"
              >
                {item.enum_options.map((opt) => (
                  <option key={opt} value={opt}>{opt}</option>
                ))}
              </select>
            ) : (
              <input
                type={item.value_type === "int" || item.value_type === "float" ? "number" : "text"}
                value={String(draft[item.key] ?? "")}
                onChange={(e) => { setDraft((d) => ({ ...d, [item.key]: e.target.value })); setDirty(true); }}
                className="w-full px-2 py-1.5 rounded-md border border-border bg-elevated text-xs text-foreground font-mono"
              />
            )}
          </div>
        </div>
      ))}
      {dirty && (
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-accent text-white text-xs font-medium hover:opacity-90 disabled:opacity-50"
        >
          <Save size={12} />
          {t("config.save")}
        </button>
      )}
    </div>
  );
}

export default function EntityDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { t } = useTranslation("entities");
  const [tab, setTab] = useState<EntityTab>("overview");
  const [PanelComponent, setPanelComponent] = useState<LazyExoticComponent<ComponentType> | null>(null);

  const { data: entity } = useQuery({
    queryKey: ["entity-detail", name],
    queryFn: () => entitiesApi.detail(name!).then((r) => r.data as EntityDetailType),
    enabled: !!name,
  });

  // 加载实体自定义面板
  useEffect(() => {
    if (!name) return;
    setPanelComponent(getEntityPanel(name));
  }, [name]);

  const toggleMutation = useMutation({
    mutationFn: (enabled: boolean) => entitiesApi.toggle(name!, enabled),
  });

  if (!entity) {
    return (
      <div className="flex items-center justify-center h-full text-sm text-muted">
        {t("loading")}
      </div>
    );
  }

  const manifest = entity.manifest;
  const displayName = manifest?.display_name || entity.group || entity.name;
  const tabs = [
    { key: "overview" as EntityTab, label: t("tabs.overview"), icon: LayoutDashboard },
    { key: "config" as EntityTab, label: t("tabs.config"), icon: Settings },
    { key: "tools" as EntityTab, label: t("tabs.tools"), icon: Wrench },
    ...(PanelComponent ? [{ key: "panel" as EntityTab, label: t("tabs.panel"), icon: PanelRight }] : []),
  ];

  return (
    <div className="h-full flex flex-col">
      {/* 头部 */}
      <div className="px-4 md:px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/tools")}
            className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
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
            <p className="text-xs text-muted mt-0.5 truncate">
              {manifest?.description || entity.description}
            </p>
          </div>
          <button
            onClick={() => toggleMutation.mutate(!entity.enabled)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all",
              entity.enabled
                ? "bg-ok-subtle text-ok border border-ok"
                : "bg-hover text-muted border border-border",
            )}
          >
            <StatusDot status={entity.enabled ? "ok" : "offline"} />
            {entity.enabled ? t("enabled") : t("disabled")}
          </button>
        </div>
      </div>

      {/* TabBar */}
      <div className="px-4 md:px-6 border-b border-border">
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
          <div className="max-w-2xl">
            <ConfigForm entity={entity} />
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
                    {tool.description && <p className="text-[10px] text-muted truncate">{tool.description}</p>}
                  </div>
                </div>
              ))
            )}
          </div>
        )}

        {tab === "panel" && PanelComponent && (
          <Suspense fallback={<div className="text-sm text-muted py-4">{t("loading")}</div>}>
            <PanelComponent />
          </Suspense>
        )}
      </div>
    </div>
  );
}
