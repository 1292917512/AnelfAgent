import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy, X } from "lucide-react";
import type { TraceNode } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { TraceStatus } from "./TraceStatus";
import { durationLabel, nodeTitle, nodeUsage, textValue } from "./trace-model";

function DataBlock({ title, value, warning = false }: { title: string; value: unknown; warning?: boolean }) {
  if (value === undefined || value === null || value === "") return null;
  const content = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return <section className="space-y-2">
    <h4 className="text-xs font-medium text-heading">{title}</h4>
    <pre className={`max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg border p-3 text-xs leading-relaxed ${warning ? "border-danger/25 bg-danger-subtle text-danger" : "border-border bg-elevated text-foreground"}`}>{content}</pre>
  </section>;
}

/** Shows complete recorded inputs, results and diagnostics without silently truncating values. */
export function NodeDetail({ node, onClose }: { node: TraceNode; onClose: () => void }) {
  const { t } = useTranslation("thinking");
  const { t: tc } = useTranslation("common");
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const usage = nodeUsage(node);
  const reasoning = textValue(node.data.reasoning_content) || textValue(node.data.reasoning_preview);
  const output = node.data.result_preview ?? node.data.content_preview ?? node.data.preview;
  const copy = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(node, null, 2)); setCopied(true); setCopyFailed(false); }
    catch { setCopyFailed(true); }
  };
  return <div className="flex h-full min-h-0 flex-col">
    <div className="flex shrink-0 items-center gap-2 border-b border-border p-4">
      <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-heading">{t("nodeDetails")}</h3>
      <Button size="icon" variant="ghost" title={t(copied ? "copied" : "copyNode")} onClick={() => void copy()}>
        {copied ? <Check size={15} /> : <Copy size={15} />}
      </Button>
      <Button size="icon" variant="ghost" title={tc("close")} onClick={onClose}><X size={16} /></Button>
    </div>
    <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-4">
      <div>
        <div className="mb-2 flex items-center gap-2 text-xs text-muted">
          <TraceStatus status={node.status} label />
          <span>· {t(`nodeTypes.${node.type}`, { defaultValue: node.type })}</span>
        </div>
        <h4 className="break-words text-sm font-semibold text-heading">{nodeTitle(node, t)}</h4>
        <p className="mt-2 text-xs text-muted">
          {new Date(node.timestamp * 1000).toLocaleString()}
          {node.duration_ms !== null && ` · ${durationLabel(node.duration_ms)}`}
        </p>
      </div>
      {copyFailed && <p role="alert" className="text-xs text-danger">{t("copyFailed")}</p>}
      <DataBlock title={t("executionError")} value={node.data.error} warning />
      <DataBlock title={t("inputArguments")} value={node.data.arguments} />
      <DataBlock title={t("recordedOutput")} value={output} />
      {reasoning && <section className="space-y-2">
        <DataBlock title={t("reasoningContent")} value={reasoning} />
        {node.data.reasoning_truncated === true && <p className="text-xs text-warn">{t("reasoningTruncated")}</p>}
      </section>}
      {usage.total_tokens > 0 && <section className="space-y-2">
        <h4 className="text-xs font-medium text-heading">{t("tokenUsage")}</h4>
        <dl className="grid grid-cols-2 gap-2 rounded-lg border border-border bg-elevated p-3">
          {([
            ["promptTokens", usage.prompt_tokens], ["completionTokens", usage.completion_tokens],
            ["totalTokens", usage.total_tokens], ["cacheRead", usage.cache_read_input_tokens],
            ["cacheCreation", usage.cache_creation_input_tokens],
          ] as const).map(([key, value]) => <div key={key}>
            <dt className="text-[11px] text-muted">{t(`detailLabels.${key}`)}</dt>
            <dd className="mt-1 text-xs font-medium tabular-nums">{value.toLocaleString()}</dd>
          </div>)}
        </dl>
      </section>}
      <details className="rounded-lg border border-border">
        <summary className="cursor-pointer p-3 text-xs font-medium text-heading">{t("rawRecord")}</summary>
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words border-t border-border p-3 text-xs leading-relaxed">{JSON.stringify(node, null, 2)}</pre>
      </details>
    </div>
  </div>;
}
