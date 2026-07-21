import { useTranslation } from "react-i18next";
import { configApi } from "@/lib/api";
import { type FieldMeta } from "@/pages/config/AppField";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";

export function ConfigPanel() {
  const { t } = useTranslation("memory");
  const { t: ta } = useTranslation("appconfig");

  const memoryFields: FieldMeta[] = [
    { key: "vector_search_batch_size", label: t("configFields.vector_search_batch_size"), type: "int", desc: t("configDescs.vector_search_batch_size") },
    { key: "memory_recall_top_k", label: t("configFields.memory_recall_top_k"), type: "int", desc: t("configDescs.memory_recall_top_k") },
    { key: "memory_recall_min_score", label: t("configFields.memory_recall_min_score"), type: "float", desc: t("configDescs.memory_recall_min_score") },
    { key: "memory_time_decay_days", label: t("configFields.memory_time_decay_days"), type: "int", desc: t("configDescs.memory_time_decay_days") },
    { key: "memory_warn_threshold", label: t("configFields.memory_warn_threshold"), type: "int", desc: t("configDescs.memory_warn_threshold") },
    { key: "memory_max_per_type", label: t("configFields.memory_max_per_type"), type: "int", desc: t("configDescs.memory_max_per_type") },
    { key: "entity_merge_threshold", label: t("configFields.entity_merge_threshold"), type: "int", desc: t("configDescs.entity_merge_threshold") },
    { key: "reflection_merge_threshold", label: t("configFields.reflection_merge_threshold"), type: "int", desc: t("configDescs.reflection_merge_threshold") },
    { key: "heartbeat_max_entries", label: t("configFields.heartbeat_max_entries"), type: "int", desc: t("configDescs.heartbeat_max_entries") },
    { key: "auto_consolidate_enabled", label: t("configFields.auto_consolidate_enabled"), type: "bool", desc: t("configDescs.auto_consolidate_enabled") },
    { key: "notes_events_retention_days", label: t("configFields.notes_events_retention_days"), type: "int", desc: t("configDescs.notes_events_retention_days") },
    { key: "notes_events_distill_enabled", label: t("configFields.notes_events_distill_enabled"), type: "bool", desc: t("configDescs.notes_events_distill_enabled") },
  ];

  const recallFields: FieldMeta[] = [
    { key: "conv_recall_scan_limit", label: ta("fields.conv_recall_scan_limit"), type: "int", desc: ta("descs.conv_recall_scan_limit") },
    { key: "conv_recall_backfill_batch", label: ta("fields.conv_recall_backfill_batch"), type: "int", desc: ta("descs.conv_recall_backfill_batch") },
    { key: "conv_recall_min_score", label: ta("fields.conv_recall_min_score"), type: "float", desc: ta("descs.conv_recall_min_score") },
    { key: "conv_recall_max_results", label: ta("fields.conv_recall_max_results"), type: "int", desc: ta("descs.conv_recall_max_results") },
  ];

  const crossChannelFields: FieldMeta[] = [
    { key: "cross_channel_enabled", label: ta("fields.cross_channel_enabled"), type: "bool", desc: ta("descs.cross_channel_enabled") },
    { key: "cross_channel_window_minutes", label: ta("fields.cross_channel_window_minutes"), type: "int", desc: ta("descs.cross_channel_window_minutes") },
    { key: "cross_channel_recall_min_score", label: ta("fields.cross_channel_recall_min_score"), type: "float", desc: ta("descs.cross_channel_recall_min_score") },
    { key: "cross_channel_recall_max_results", label: ta("fields.cross_channel_recall_max_results"), type: "int" },
    { key: "cross_channel_recall_scan_limit", label: ta("fields.cross_channel_recall_scan_limit"), type: "int" },
    { key: "cross_channel_narrative_max_items", label: ta("fields.cross_channel_narrative_max_items"), type: "int", desc: ta("descs.cross_channel_narrative_max_items") },
  ];

  return (
    <div className="space-y-4">
      <ConfigFormPanel
        title={t("memoryConfig")}
        subtitle={t("memoryConfigSubtitle")}
        fields={memoryFields}
        queryKey="mindConfig"
        fetchFn={() => configApi.getMind().then((r) => r.data?.config || r.data)}
        saveFn={(values) => configApi.saveMind(values)}
      />
      <ConfigFormPanel
        title={ta("sections.deepRecall")}
        subtitle={ta("sections.deepRecallSubtitle")}
        fields={recallFields}
        queryKey="appConfig"
        fetchFn={() => configApi.getApp().then((r) => r.data)}
        saveFn={(values) => configApi.saveApp(values)}
        extraInvalidateKeys={["configSnapshot"]}
        note={ta("notes.restartRequired")}
      />
      <ConfigFormPanel
        title={ta("sections.crossChannel")}
        subtitle={ta("sections.crossChannelSubtitle")}
        fields={crossChannelFields}
        queryKey="mindConfig"
        fetchFn={() => configApi.getMind().then((r) => r.data?.config || r.data)}
        saveFn={(values) => configApi.saveMind(values)}
      />
    </div>
  );
}
