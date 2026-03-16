import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import type { TraceNode } from "@/stores/thinking-store";

interface Props {
  node: TraceNode;
  onClose: () => void;
}

export function NodeDetail({ node, onClose }: Props) {
  const { t } = useTranslation("thinking");
  const ts = new Date(node.timestamp * 1000);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border)]">
        <h3 className="text-sm font-semibold text-[var(--text-strong)] truncate">
          {t(`nodeTypes.${node.type}`, { defaultValue: node.type })}
        </h3>
        <button
          onClick={onClose}
          className="p-1 rounded-[var(--radius-sm)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs">
        <div className="space-y-1.5">
          <Row label={t("detailLabels.label")} value={node.label} />
          <Row label={t("detailLabels.status")} value={t(`statusLabels.${node.status}`, { defaultValue: node.status })} />
          <Row label={t("detailLabels.time")} value={ts.toLocaleTimeString()} />
          {node.duration_ms != null && (
            <Row
              label={t("detailLabels.duration")}
              value={
                node.duration_ms >= 1000
                  ? `${(node.duration_ms / 1000).toFixed(2)}s`
                  : `${Math.round(node.duration_ms)}ms`
              }
            />
          )}
          <Row label="ID" value={node.id} mono />
        </div>

        {node.type === "llm_call" && (() => {
          const usage = node.data.usage as { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } | undefined;
          const pct = node.data.usage_percent as number | undefined;
          const maxTokens = node.data.max_tokens as number | undefined;
          if (!usage?.total_tokens) return null;
          return (
            <div className="space-y-1.5">
              <Row label={t("detailLabels.promptTokens", { defaultValue: "Prompt Tokens" })} value={String(usage.prompt_tokens ?? 0)} mono />
              <Row label={t("detailLabels.completionTokens", { defaultValue: "Completion Tokens" })} value={String(usage.completion_tokens ?? 0)} mono />
              <Row label={t("detailLabels.totalTokens", { defaultValue: "Total Tokens" })} value={String(usage.total_tokens)} mono />
              {maxTokens != null && maxTokens > 0 && (
                <Row label={t("detailLabels.maxTokens", { defaultValue: "Max Tokens" })} value={String(maxTokens)} mono />
              )}
              {pct != null && (
                <Row label={t("detailLabels.usagePercent", { defaultValue: "Usage" })} value={`${pct}%`} mono />
              )}
            </div>
          );
        })()}

        {node.type === "llm_call" && typeof node.data.reasoning_preview === "string" && node.data.reasoning_preview && (
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-strong)] mb-1.5">
              {t("reasoningContent")}
            </div>
            <div className="rounded-[var(--radius-sm)] bg-purple-500/5 border border-purple-500/30 p-2.5 text-xs text-[var(--text)] whitespace-pre-wrap leading-relaxed max-h-64 overflow-y-auto">
              {node.data.reasoning_preview}
            </div>
          </div>
        )}

        {node.data && Object.keys(node.data).length > 0 && (
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-strong)] mb-1.5">
              {t("detailData")}
            </div>
            <div className="rounded-[var(--radius-sm)] bg-[var(--bg-elevated)] border border-[var(--border)] p-2.5 overflow-x-auto">
              <DataTree data={node.data} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-[var(--muted)] shrink-0">{label}</span>
      <span className={`text-[var(--text)] text-right truncate ${mono ? "font-mono" : ""}`}>
        {value}
      </span>
    </div>
  );
}

function DataTree({ data, depth = 0 }: { data: unknown; depth?: number }) {
  if (data === null || data === undefined) {
    return <span className="text-[var(--muted)] italic">null</span>;
  }

  if (typeof data === "string") {
    if (data.length > 200) {
      return (
        <span className="text-[var(--ok)] font-mono break-all whitespace-pre-wrap">
          "{data.slice(0, 200)}..."
        </span>
      );
    }
    return <span className="text-[var(--ok)] font-mono break-all">"{data}"</span>;
  }

  if (typeof data === "number" || typeof data === "boolean") {
    return <span className="text-[var(--accent)] font-mono">{String(data)}</span>;
  }

  if (Array.isArray(data)) {
    if (data.length === 0) return <span className="text-[var(--muted)] font-mono">[]</span>;
    return (
      <div className="space-y-0.5" style={{ paddingLeft: depth > 0 ? 12 : 0 }}>
        {data.map((item, i) => (
          <div key={`arr-${i}`} className="flex gap-1">
            <span className="text-[var(--muted)] font-mono shrink-0">{i}:</span>
            <DataTree data={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  if (typeof data === "object") {
    const entries = Object.entries(data as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-[var(--muted)] font-mono">{"{}"}</span>;
    return (
      <div className="space-y-0.5" style={{ paddingLeft: depth > 0 ? 12 : 0 }}>
        {entries.map(([key, val]) => (
          <div key={key} className="flex gap-1">
            <span className="text-purple-400 font-mono shrink-0">{key}:</span>
            <DataTree data={val} depth={depth + 1} />
          </div>
        ))}
      </div>
    );
  }

  return <span className="font-mono">{String(data)}</span>;
}
