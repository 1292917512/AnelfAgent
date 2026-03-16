import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { configApi } from "@/lib/api";
import { TabBar, type TabItem } from "@/components/common/TabBar";
import { Brain, Database, Zap, Settings2 } from "lucide-react";
import { type FieldMeta } from "@/pages/config/AppField";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import { IntrospectionConfigPanel } from "@/pages/config/IntrospectionConfigPanel";
import { IntrospectionUnitsPanel } from "@/pages/config/IntrospectionUnitsPanel";
import { TasksPanel } from "@/pages/config/TasksPanel";
import { LiteLLMCostMapCard } from "@/pages/config/LiteLLMCostMapCard";
import { ToolSystemRulesPanel } from "@/pages/config/ToolSystemRulesPanel";

type ConfigTab = "thinking" | "memory" | "behavior" | "system";

export default function AppConfig() {
  const { t } = useTranslation("settings");
  const [activeTab, setActiveTab] = useState<ConfigTab>("thinking");

  const CONFIG_TABS: TabItem<ConfigTab>[] = [
    { key: "thinking", label: t("configTabs.thinking"), icon: Brain },
    { key: "memory", label: t("configTabs.memory"), icon: Database },
    { key: "behavior", label: t("configTabs.behavior"), icon: Zap },
    { key: "system", label: t("configTabs.system"), icon: Settings2 },
  ];

  return (
    <div className="space-y-6 max-w-6xl">
      <TabBar tabs={CONFIG_TABS} activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "thinking" && <ThinkingTab />}
      {activeTab === "memory" && <MemoryTab />}
      {activeTab === "behavior" && <BehaviorTab />}
      {activeTab === "system" && <SystemTab />}
    </div>
  );
}

// ── Tab 组合 ──────────────────────────────────────────────────────────────────

function ThinkingTab() {
  const { t } = useTranslation("appconfig");

  const thinkingFields: FieldMeta[] = [
    { key: "heartbeat_interval", label: t("fields.heartbeat_interval"), type: "float" },
    { key: "meta_decision_temperature", label: t("fields.meta_decision_temperature"), type: "float", desc: t("descs.meta_decision_temperature") },
    { key: "conversation_analysis_threshold", label: t("fields.conversation_analysis_threshold"), type: "int" },
    { key: "max_tool_iterations", label: t("fields.max_tool_iterations"), type: "int", desc: t("descs.max_tool_iterations") },
    { key: "log_ai_output", label: t("fields.log_ai_output"), type: "bool" },
    { key: "send_interim_text", label: t("fields.send_interim_text"), type: "bool" },
    { key: "short_term_memory_size", label: t("fields.short_term_memory_size"), type: "int", desc: t("descs.short_term_memory_size") },
    { key: "tool_recall_top_n", label: t("fields.tool_recall_top_n"), type: "int" },
    { key: "llm_timeout", label: t("fields.llm_timeout"), type: "float" },
    { key: "llm_max_retries", label: t("fields.llm_max_retries"), type: "int" },
  ];

  const coreFields: FieldMeta[] = [
    { key: "max_conversation_size", label: t("fields.max_conversation_size"), type: "int", desc: t("descs.max_conversation_size") },
    { key: "llm_stream_enabled", label: t("fields.llm_stream_enabled"), type: "bool" },
    { key: "sandbox_enabled", label: t("fields.sandbox_enabled"), type: "bool" },
  ];

  return (
    <div className="space-y-4">
      <ConfigFormPanel
        title={t("sections.thinkingParams")}
        subtitle={t("sections.thinkingParamsSubtitle")}
        fields={thinkingFields}
        queryKey="mindConfig"
        fetchFn={() => configApi.getMind().then((r) => r.data?.config || r.data)}
        saveFn={(values) => configApi.saveMind(values)}
      />
      <ConfigFormPanel
        title={t("sections.coreBehavior")}
        fields={coreFields}
        queryKey="appConfig"
        fetchFn={() => configApi.getApp().then((r) => r.data)}
        saveFn={(values) => configApi.saveApp(values)}
        extraInvalidateKeys={["configSnapshot"]}
        note={t("notes.restartRequired")}
      />
      <ToolSystemRulesPanel />
    </div>
  );
}

function MemoryTab() {
  const { t } = useTranslation("appconfig");

  const memoryFields: FieldMeta[] = [
    { key: "vector_search_batch_size", label: t("fields.vector_search_batch_size"), type: "int", desc: t("descs.vector_search_batch_size") },
    { key: "memory_recall_top_k", label: t("fields.memory_recall_top_k"), type: "int", desc: t("descs.memory_recall_top_k") },
    { key: "memory_recall_min_score", label: t("fields.memory_recall_min_score"), type: "float", desc: t("descs.memory_recall_min_score") },
    { key: "memory_time_decay_days", label: t("fields.memory_time_decay_days"), type: "int", desc: t("descs.memory_time_decay_days") },
    { key: "memory_warn_threshold", label: t("fields.memory_warn_threshold"), type: "int", desc: t("descs.memory_warn_threshold") },
    { key: "memory_max_per_type", label: t("fields.memory_max_per_type"), type: "int", desc: t("descs.memory_max_per_type") },
    { key: "entity_merge_threshold", label: t("fields.entity_merge_threshold"), type: "int", desc: t("descs.entity_merge_threshold") },
    { key: "reflection_merge_threshold", label: t("fields.reflection_merge_threshold"), type: "int", desc: t("descs.reflection_merge_threshold") },
    { key: "heartbeat_max_entries", label: t("fields.heartbeat_max_entries"), type: "int", desc: t("descs.heartbeat_max_entries") },
    { key: "auto_consolidate_enabled", label: t("fields.auto_consolidate_enabled"), type: "bool", desc: t("descs.auto_consolidate_enabled") },
  ];

  const recallFields: FieldMeta[] = [
    { key: "conv_recall_scan_limit", label: t("fields.conv_recall_scan_limit"), type: "int", desc: t("descs.conv_recall_scan_limit") },
    { key: "conv_recall_backfill_batch", label: t("fields.conv_recall_backfill_batch"), type: "int", desc: t("descs.conv_recall_backfill_batch") },
    { key: "conv_recall_min_score", label: t("fields.conv_recall_min_score"), type: "float", desc: t("descs.conv_recall_min_score") },
    { key: "conv_recall_max_results", label: t("fields.conv_recall_max_results"), type: "int", desc: t("descs.conv_recall_max_results") },
  ];

  const crossChannelFields: FieldMeta[] = [
    { key: "cross_channel_enabled", label: t("fields.cross_channel_enabled"), type: "bool", desc: t("descs.cross_channel_enabled") },
    { key: "cross_channel_window_minutes", label: t("fields.cross_channel_window_minutes"), type: "int", desc: t("descs.cross_channel_window_minutes") },
    { key: "cross_channel_recall_min_score", label: t("fields.cross_channel_recall_min_score"), type: "float", desc: t("descs.cross_channel_recall_min_score") },
    { key: "cross_channel_recall_max_results", label: t("fields.cross_channel_recall_max_results"), type: "int" },
    { key: "cross_channel_recall_scan_limit", label: t("fields.cross_channel_recall_scan_limit"), type: "int" },
    { key: "cross_channel_narrative_max_items", label: t("fields.cross_channel_narrative_max_items"), type: "int", desc: t("descs.cross_channel_narrative_max_items") },
  ];

  return (
    <div className="space-y-4">
      <ConfigFormPanel
        title={t("sections.memoryConfig")}
        subtitle={t("sections.memoryConfigSubtitle")}
        fields={memoryFields}
        queryKey="mindConfig"
        fetchFn={() => configApi.getMind().then((r) => r.data?.config || r.data)}
        saveFn={(values) => configApi.saveMind(values)}
      />
      <ConfigFormPanel
        title={t("sections.deepRecall")}
        subtitle={t("sections.deepRecallSubtitle")}
        fields={recallFields}
        queryKey="appConfig"
        fetchFn={() => configApi.getApp().then((r) => r.data)}
        saveFn={(values) => configApi.saveApp(values)}
        extraInvalidateKeys={["configSnapshot"]}
        note={t("notes.restartRequired")}
      />
      <ConfigFormPanel
        title={t("sections.crossChannel")}
        subtitle={t("sections.crossChannelSubtitle")}
        fields={crossChannelFields}
        queryKey="mindConfig"
        fetchFn={() => configApi.getMind().then((r) => r.data?.config || r.data)}
        saveFn={(values) => configApi.saveMind(values)}
      />
    </div>
  );
}

function BehaviorTab() {
  return (
    <div className="space-y-4">
      <IntrospectionConfigPanel />
      <IntrospectionUnitsPanel />
      <TasksPanel />
    </div>
  );
}

function SystemTab() {
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
