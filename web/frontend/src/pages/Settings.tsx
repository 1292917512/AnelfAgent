import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Card } from "@/components/common/Card";
import { StatCard } from "@/components/common/StatCard";
import { useAppStore } from "@/stores/app-store";
import { systemApi, configApi } from "@/lib/api";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Database, Check, X, TestTube } from "lucide-react";
import { type FieldMeta } from "@/pages/config/AppField";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { LiteLLMCostMapCard } from "@/pages/config/LiteLLMCostMapCard";

type SettingsTab = "sysConfig" | "system" | "python" | "git" | "config";

export default function Settings() {
  const { t } = useTranslation("settings");
  const [tab, setTab] = useState<SettingsTab>("sysConfig");

  const TAB_KEYS: TabItem<SettingsTab>[] = [
    { key: "sysConfig", label: t("tabs.sysConfig") },
    { key: "system", label: t("tabs.system") },
    { key: "python", label: t("tabs.python") },
    { key: "git", label: t("tabs.git") },
    { key: "config", label: t("tabs.config") },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      <TabBar tabs={TAB_KEYS} activeTab={tab} onChange={setTab} />

      {tab === "sysConfig" && <SysConfigPanel />}
      {tab === "system" && <SystemPanel />}
      {tab === "python" && <PythonPanel />}
      {tab === "git" && <GitPanel />}
      {tab === "config" && <ConfigPanel />}
    </div>
  );
}

function SysConfigPanel() {
  const { t } = useTranslation("appconfig");

  const { data } = useQuery({
    queryKey: ["appConfig"],
    queryFn: () => configApi.getApp().then((r) => r.data),
  });

  const proxyUrl = typeof data?.["https_proxy"] === "string" ? (data["https_proxy"] as string) : "";

  const networkFields: FieldMeta[] = [
    { key: "proxy_enabled", label: t("fields.proxy_enabled"), type: "bool" },
    { key: "http_proxy", label: t("fields.http_proxy"), type: "string", desc: t("descs.http_proxy") },
    { key: "https_proxy", label: t("fields.https_proxy"), type: "string", desc: t("descs.https_proxy") },
    { key: "connect_timeout", label: t("fields.connect_timeout"), type: "float" },
    { key: "read_timeout", label: t("fields.read_timeout"), type: "float" },
    { key: "total_timeout", label: t("fields.total_timeout"), type: "float" },
    { key: "retry_count", label: t("fields.retry_count"), type: "int" },
    { key: "retry_delay", label: t("fields.retry_delay"), type: "float" },
    { key: "backoff_factor", label: t("fields.backoff_factor"), type: "float" },
    { key: "chunk_size", label: t("fields.chunk_size"), type: "int" },
    { key: "user_agent", label: t("fields.user_agent"), type: "string" },
    { key: "overwrite_existing", label: t("fields.overwrite_existing"), type: "bool" },
    { key: "verify_download", label: t("fields.verify_download"), type: "bool" },
    { key: "default_download_dir", label: t("fields.default_download_dir"), type: "string" },
    { key: "workspace_root", label: t("fields.workspace_root"), type: "string" },
  ];

  return (
    <div className="space-y-4">
      <LiteLLMCostMapCard defaultProxy={proxyUrl} />
      <ConfigFormPanel
        title={t("sections.network")}
        fields={networkFields}
        queryKey="appConfig"
        fetchFn={() => configApi.getApp().then((r) => r.data)}
        saveFn={(values) => configApi.saveApp(values)}
        extraInvalidateKeys={["configSnapshot"]}
        note={t("notes.restartRequired")}
      />
    </div>
  );
}

function SystemPanel() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const { data, isLoading } = useQuery({
    queryKey: ["systemInfo"],
    queryFn: () => systemApi.info().then((r) => r.data),
  });

  if (isLoading) return <Card><p className="text-sm text-[var(--muted)]">{tc("loading")}</p></Card>;

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
              {[
                [t("processor"), sys.processor],
                [t("user"), sys.user],
                [t("homeDir"), sys.home],
                ["Shell", sys.shell],
                ...(sys.disk_total_gb ? [[t("disk"), `${sys.disk_used_gb} / ${sys.disk_total_gb} GB`]] : []),
              ].map(([k, v]) => (
                <div key={k as string} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                  <span className="text-xs text-[var(--muted)]">{k}</span>
                  <span className="text-sm text-[var(--text-strong)] font-mono">{v}</span>
                </div>
              ))}
            </div>
          </Card>
        </>
      )}

      {py && (
        <Card title={t("pythonEnv")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[
              [t("version"), py.version],
              [t("implementation"), py.implementation],
              [t("path"), py.executable],
              [t("virtualEnv"), py.in_venv ? py.venv_path : t("systemEnv")],
            ].map(([k, v]) => (
              <div key={k as string} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                <span className="text-xs text-[var(--muted)]">{k}</span>
                <span className="text-sm text-[var(--text-strong)] font-mono truncate ml-2">{v as string}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title={t("devTools")} subtitle={`${installed.length} ${t("installed")} / ${missing.length} ${t("notInstalled")}`}>
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
          {tools.map((tool) => (
            <div key={tool.name} className="flex items-center gap-2 p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
              {tool.installed
                ? <Check size={14} className="text-[var(--ok)] flex-shrink-0" />
                : <X size={14} className="text-[var(--muted)] flex-shrink-0" />}
              <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--text-strong)]">{tool.name}</p>
                {tool.version && <p className="text-[11px] text-[var(--muted)] truncate">{tool.version}</p>}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function PythonPanel() {
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
                <div key={k} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                  <span className="text-xs text-[var(--muted)] font-mono">{k}</span>
                  <span className="text-sm text-[var(--text-strong)]">{String(v)}</span>
                </div>
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
                <div key={k} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                  <span className="text-xs text-[var(--muted)]">{k}</span>
                  <span className="text-sm text-[var(--text-strong)] truncate ml-2 font-mono">{String(v)}</span>
                </div>
              ))}
          </div>
        </Card>
      )}

      <Card title={t("installedPackages")} subtitle={t("nPackages", { count: (packages as unknown[])?.length ?? 0 })}>
        <div className="max-h-80 overflow-y-auto space-y-1">
          {(packages as Array<{ name: string; version: string }> ?? []).map((p) => (
            <div key={p.name} className="flex items-center justify-between py-1.5 px-3 rounded-[var(--radius-sm)] hover:bg-[var(--bg-hover)] transition-colors">
              <span className="text-sm text-[var(--text)]">{p.name}</span>
              <span className="text-xs text-[var(--muted)] font-mono">{p.version}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function GitPanel() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const [testResult, setTestResult] = useState<string>("");

  const { data: config } = useQuery({
    queryKey: ["gitConfig"],
    queryFn: () => systemApi.git().then((r) => r.data),
  });

  const testMutation = useMutation({
    mutationFn: () => systemApi.testGithub().then((r) => r.data),
    onSuccess: (data) => setTestResult(JSON.stringify(data, null, 2)),
  });

  return (
    <div className="space-y-4">
      <Card title={t("gitGlobalConfig")} actions={
        <button
          onClick={() => testMutation.mutate()}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
            border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)]
            hover:bg-[var(--bg-hover)] transition-all"
        >
          <TestTube size={14} /> {t("testGithub")}
        </button>
      }>
        {config ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(config as Record<string, string>).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                <span className="text-xs text-[var(--muted)] font-mono">{k}</span>
                <span className="text-sm text-[var(--text-strong)]">{v || "—"}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-[var(--muted)]">{tc("loading")}</p>
        )}
      </Card>

      {testResult && (
        <Card title={t("githubConnectivity")}>
          <pre className="text-xs font-mono text-[var(--text)] bg-[var(--bg-elevated)] border border-[var(--border)] rounded-[var(--radius-md)] p-3 overflow-auto max-h-48">
            {testResult}
          </pre>
        </Card>
      )}
    </div>
  );
}

function ConfigPanel() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const branding = useAppStore((s) => s.branding);
  const { data: snapshot } = useQuery({
    queryKey: ["configSnapshot"],
    queryFn: () => configApi.snapshot().then((r) => r.data),
  });

  const configItems: { key: string; file: string }[] = [
    { key: "app", file: "app_config.json" },
    { key: "mind", file: "mind_config.json" },
    { key: "llm", file: "llm_clients.json" },
    { key: "mcp", file: "mcp_servers.json" },
    { key: "personas", file: "personas.json" },
  ];

  const mindConfig = snapshot?.mind as Record<string, unknown> | undefined;

  return (
    <div className="space-y-4">
      <Card title={t("configFileStatus")}>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {configItems.map((item) => (
            <div
              key={item.key}
              className="flex items-center gap-3 p-3 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]"
            >
              <Database size={16} className={snapshot?.[item.key] ? "text-[var(--ok)]" : "text-[var(--muted)]"} />
              <div className="min-w-0">
                <p className="text-sm font-medium text-[var(--text-strong)]">{t(`configLabels.${item.key}`)}</p>
                <p className="text-xs text-[var(--muted)] font-mono truncate">{item.file}</p>
              </div>
              <span className={`ml-auto text-xs font-medium px-2 py-0.5 rounded-full ${
                snapshot?.[item.key]
                  ? "bg-[var(--ok-subtle)] text-[var(--ok)]"
                  : "bg-[var(--secondary)] text-[var(--muted)]"
              }`}>
                {snapshot?.[item.key] ? tc("loaded") : tc("notFound")}
              </span>
            </div>
          ))}
        </div>
      </Card>

      {mindConfig && (
        <Card title={t("configLabels.mind")}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {Object.entries(mindConfig)
              .filter(([, v]) => typeof v === "string" || typeof v === "number" || typeof v === "boolean")
              .map(([k, v]) => (
                <div key={k} className="flex items-center justify-between p-2.5 rounded-[var(--radius-md)] bg-[var(--bg-elevated)] border border-[var(--border)]">
                  <span className="text-xs text-[var(--muted)]">{t(`mindFields.${k}`, { defaultValue: k })}</span>
                  <span className="text-sm text-[var(--text-strong)] font-mono">{String(v)}</span>
                </div>
              ))}
          </div>
        </Card>
      )}

      <Card title={t("about")}>
        <div className="space-y-2 text-sm">
          <p className="text-[var(--text)]">
            <span className="font-semibold text-[var(--text-strong)]">{branding.title}</span> — {branding.subtitle}
          </p>
          <p className="text-[var(--muted)]">{t("versionInfo", { version: branding.version })}</p>
          <p className="text-[var(--muted)]">{t("webuiInfo")}</p>
        </div>
      </Card>
    </div>
  );
}
