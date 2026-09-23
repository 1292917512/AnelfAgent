import { useTranslation } from "react-i18next";
import type { WorkflowRunDetail } from "@/lib/api";
import { Badge } from "@/components/ui";
import { cn } from "@/lib/utils";

/** 运行详情：步骤状态（按 key 折叠重试轮次）+ 事件时间线（父面板轮询刷新）。 */
export function RunDetail({ detail }: { detail: WorkflowRunDetail }) {
  const { t } = useTranslation("workflow");

  const nodesByKey = new Map<string, typeof detail.nodes>();
  for (const node of detail.nodes) {
    nodesByKey.set(node.key, [...(nodesByKey.get(node.key) ?? []), node]);
  }
  // 声明序无法从 detail 恢复，按首现顺序展示
  const keys = [...nodesByKey.keys()];

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-border bg-card p-4 space-y-2">
        <div className="text-sm font-medium text-heading">{t("detail.steps")}</div>
        <div className="space-y-2">
          {keys.map((key) => {
            const rounds = nodesByKey.get(key) ?? [];
            const latest = rounds[rounds.length - 1];
            if (!latest) return null;
            return (
              <div key={key} className="rounded border border-border p-2.5 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-medium text-heading">{key}</span>
                  <Badge variant="neutral">{latest.kind}</Badge>
                  {rounds.length > 1 && (
                    <Badge variant="warn">
                      {t("detail.rounds", { count: rounds.length })}
                    </Badge>
                  )}
                  <span
                    className={cn(
                      "text-[11px]",
                      latest.status === "completed"
                        ? "text-ok"
                        : latest.status === "running"
                          ? "text-warn"
                          : "text-danger",
                    )}
                  >
                    {t(`nodeStatus.${latest.status}`)}
                  </span>
                </div>
                {latest.result_preview && (
                  <pre className="text-[11px] text-muted whitespace-pre-wrap break-all max-h-32 overflow-y-auto">
                    {latest.result_preview}
                  </pre>
                )}
                {latest.error && (
                  <pre className="text-[11px] text-danger whitespace-pre-wrap break-all">
                    {latest.error}
                  </pre>
                )}
              </div>
            );
          })}
          {keys.length === 0 && (
            <div className="text-xs text-muted py-3 text-center">
              {t("detail.noSteps")}
            </div>
          )}
        </div>
      </div>

      <div className="rounded-lg border border-border bg-card p-4 space-y-2">
        <div className="text-sm font-medium text-heading">{t("detail.timeline")}</div>
        <div className="space-y-1 max-h-72 overflow-y-auto font-mono text-[11px]">
          {[...detail.events].reverse().map((event) => (
            <div key={event.sequence} className="flex gap-2">
              <span className="text-muted shrink-0 w-8 text-right">
                #{event.sequence}
              </span>
              <span
                className={cn(
                  "shrink-0",
                  event.type === "run-settled" ? "text-accent" : "text-heading",
                )}
              >
                {event.type}
              </span>
              <span className="text-muted truncate">
                {describeEvent(event.type, event.payload)}
              </span>
            </div>
          ))}
          {detail.events.length === 0 && (
            <div className="text-muted text-center py-3">{t("detail.noEvents")}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function describeEvent(type: string, payload: Record<string, unknown>): string {
  const key = String(payload.key ?? "");
  const ordinal = payload.ordinal != null ? `@${payload.ordinal}` : "";
  switch (type) {
    case "run-started":
      return String(payload.name ?? "");
    case "node-admitted":
      return `${key}${ordinal} ${String(payload.kind ?? "")}`;
    case "node-settled":
      return `${key}${ordinal} ${String(payload.outcome ?? "")}${payload.cached ? " (cached)" : ""}${
        payload.error ? ` · ${String(payload.error)}` : ""
      }`;
    case "run-settled":
      return `${String(payload.status ?? "")}${payload.stop_reason ? ` · ${String(payload.stop_reason)}` : ""}`;
    default:
      return "";
  }
}
