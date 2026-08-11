import { useTranslation } from "react-i18next";
import { configMetaApi } from "@/lib/api";
import { type FieldMeta } from "@/pages/config/AppField";
import { ConfigFormPanel } from "@/pages/config/ConfigFormPanel";
import type { ConfigValues } from "@/lib/types";

/** 经统一配置元数据 API 读取指定 keys 的当前值 */
const metaFetch = (keys: string[]) => async (): Promise<ConfigValues> => {
  const { data } = await configMetaApi.list();
  const items = new Map(data.groups.flatMap((g) => g.items.map((i) => [i.key, i.value])));
  return Object.fromEntries(keys.map((k) => [k, items.get(k) ?? null]));
};

/** 经统一配置元数据 API 保存（热更生效；Mind 字段服务端自动路由双轨同步） */
const metaSave = async (values: ConfigValues) => {
  await Promise.all(Object.entries(values).map(([k, v]) => configMetaApi.save(k, v)));
};

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

  const embeddingFields: FieldMeta[] = [
    { key: "embedding_text_model", label: ta("fields.embedding_text_model"), type: "model", modelType: "embedding", desc: ta("descs.embedding_text_model") },
    { key: "embedding_vision_model", label: ta("fields.embedding_vision_model"), type: "model", modelType: "embedding", desc: ta("descs.embedding_vision_model") },
    { key: "embed_query_timeout_seconds", label: ta("fields.embed_query_timeout_seconds"), type: "float", desc: ta("descs.embed_query_timeout_seconds") },
    { key: "embed_query_cache_ttl_seconds", label: ta("fields.embed_query_cache_ttl_seconds"), type: "float", desc: ta("descs.embed_query_cache_ttl_seconds") },
    { key: "embed_rate_limit_requests", label: ta("fields.embed_rate_limit_requests"), type: "int", desc: ta("descs.embed_rate_limit_requests") },
    { key: "embed_rate_limit_interval_seconds", label: ta("fields.embed_rate_limit_interval_seconds"), type: "float", desc: ta("descs.embed_rate_limit_interval_seconds") },
    { key: "embed_max_retries", label: ta("fields.embed_max_retries"), type: "int", desc: ta("descs.embed_max_retries") },
    { key: "embedding_worker_batch_size", label: ta("fields.embedding_worker_batch_size"), type: "int", desc: ta("descs.embedding_worker_batch_size") },
    { key: "conv_embed_backfill_days", label: ta("fields.conv_embed_backfill_days"), type: "int", desc: ta("descs.conv_embed_backfill_days") },
  ];

  const crossChannelFields: FieldMeta[] = [
    { key: "cross_channel_enabled", label: ta("fields.cross_channel_enabled"), type: "bool", desc: ta("descs.cross_channel_enabled") },
    { key: "cross_channel_window_minutes", label: ta("fields.cross_channel_window_minutes"), type: "int", desc: ta("descs.cross_channel_window_minutes") },
    { key: "cross_channel_recall_min_score", label: ta("fields.cross_channel_recall_min_score"), type: "float", desc: ta("descs.cross_channel_recall_min_score") },
    { key: "cross_channel_recall_max_results", label: ta("fields.cross_channel_recall_max_results"), type: "int" },
    { key: "cross_channel_recall_scan_limit", label: ta("fields.cross_channel_recall_scan_limit"), type: "int" },
    { key: "cross_channel_narrative_max_items", label: ta("fields.cross_channel_narrative_max_items"), type: "int", desc: ta("descs.cross_channel_narrative_max_items") },
  ];

  const panels: { title: string; subtitle: string; fields: FieldMeta[] }[] = [
    { title: t("memoryConfig"), subtitle: t("memoryConfigSubtitle"), fields: memoryFields },
    { title: ta("sections.deepRecall"), subtitle: ta("sections.deepRecallSubtitle"), fields: recallFields },
    { title: ta("sections.embeddingEngine"), subtitle: ta("sections.embeddingEngineSubtitle"), fields: embeddingFields },
    { title: ta("sections.crossChannel"), subtitle: ta("sections.crossChannelSubtitle"), fields: crossChannelFields },
  ];

  return (
    <div className="space-y-4">
      {panels.map((p) => (
        <ConfigFormPanel
          key={p.title}
          title={p.title}
          subtitle={p.subtitle}
          fields={p.fields}
          queryKey="configMeta"
          fetchFn={metaFetch(p.fields.map((f) => f.key))}
          saveFn={metaSave}
          extraInvalidateKeys={["configSnapshot"]}
        />
      ))}
    </div>
  );
}
