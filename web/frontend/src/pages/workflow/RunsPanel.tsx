import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, RotateCcw, Square } from "lucide-react";
import { apiErrorMessage, workflowApi } from "@/lib/api";
import type { WorkflowRun, WorkflowRunDetail } from "@/lib/api";
import { StatusDot } from "@/components/common/StatusDot";
import { Badge, Button, toast } from "@/components/ui";
import { cn } from "@/lib/utils";
import { RunDetail } from "./RunDetail";

const SPEC_PLACEHOLDER = `{
  "name": "并行调研并汇总",
  "steps": [
    { "key": "topic_a", "kind": "ask", "goal": "调研主题 A 并输出要点" },
    { "key": "topic_b", "kind": "ask", "goal": "调研主题 B 并输出要点" },
    { "key": "summary", "kind": "ask", "depends_on": ["topic_a", "topic_b"],
      "goal": "汇总上游结果为结构化报告" }
  ]
}`;

/** 工作流运行面板：启动表单 + 运行列表 + 选中详情。 */
export function RunsPanel({
  selected,
  onSelect,
}: {
  selected: WorkflowRun | null;
  onSelect: (run: WorkflowRun | null) => void;
}) {
  const { t } = useTranslation("workflow");
  const queryClient = useQueryClient();
  const [specText, setSpecText] = useState("");
  const [resumeOf, setResumeOf] = useState("");

  const { data: runs = [], isLoading } = useQuery({
    queryKey: ["workflowRuns"],
    queryFn: () => workflowApi.listRuns().then((r) => r.data.runs),
    refetchInterval: 4000,
  });

  const activeRun = selected
    ? runs.find((r) => r.run_id === selected.run_id) ?? selected
    : null;
  const polling = !!activeRun?.running;

  const { data: detail } = useQuery({
    queryKey: ["workflowRunDetail", activeRun?.run_id],
    queryFn: () => workflowApi.runDetail(activeRun!.run_id).then((r) => r.data),
    enabled: !!activeRun,
    refetchInterval: polling ? 4000 : false,
  });

  useEffect(() => {
    if (activeRun && !activeRun.running && polling) {
      queryClient.invalidateQueries({ queryKey: ["workflowRuns"] });
    }
  }, [activeRun, polling, queryClient]);

  const startMutation = useMutation({
    mutationFn: () => {
      const spec = JSON.parse(specText);
      return workflowApi.startRun(spec, resumeOf.trim()).then((r) => r.data);
    },
    onSuccess: (run) => {
      toast.success(t("toast.started", { name: run.name }));
      setSpecText("");
      setResumeOf("");
      queryClient.invalidateQueries({ queryKey: ["workflowRuns"] });
    },
    onError: (err) => {
      toast.error(apiErrorMessage(err, t("toast.requestFailed")));
    },
  });

  const stopMutation = useMutation({
    mutationFn: (runId: string) => workflowApi.stopRun(runId).then((r) => r.data),
    onSuccess: () => {
      toast.success(t("toast.stopped"));
      queryClient.invalidateQueries({ queryKey: ["workflowRuns"] });
      queryClient.invalidateQueries({ queryKey: ["workflowRunDetail"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  const resumeMutation = useMutation({
    mutationFn: (runId: string) => workflowApi.resumeRun(runId).then((r) => r.data),
    onSuccess: () => {
      toast.success(t("toast.resumed"));
      queryClient.invalidateQueries({ queryKey: ["workflowRuns"] });
      queryClient.invalidateQueries({ queryKey: ["workflowRunDetail"] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("toast.requestFailed"))),
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="space-y-4">
        <div className="rounded-lg border border-border bg-card p-4 space-y-3">
          <div className="text-sm font-medium text-heading">{t("start.title")}</div>
          <textarea
            value={specText}
            onChange={(e) => setSpecText(e.target.value)}
            placeholder={SPEC_PLACEHOLDER}
            spellCheck={false}
            rows={10}
            className="w-full rounded border border-border bg-background p-2 font-mono text-xs text-foreground resize-y focus:outline-none focus:border-accent"
          />
          <div className="flex items-center gap-2">
            <input
              value={resumeOf}
              onChange={(e) => setResumeOf(e.target.value)}
              placeholder={t("start.resumeOf")}
              className="flex-1 rounded border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus:border-accent"
            />
            <Button
              variant="primary"
              size="sm"
              disabled={startMutation.isPending || !specText.trim()}
              onClick={() => startMutation.mutate()}
            >
              {startMutation.isPending ? (
                <Loader2 size={14} className="animate-spin" />
              ) : (
                <Play size={14} />
              )}
              {t("start.submit")}
            </Button>
          </div>
        </div>

        <div className="rounded-lg border border-border bg-card p-4 space-y-2">
          <div className="text-sm font-medium text-heading">{t("list.title")}</div>
          {isLoading ? (
            <div className="text-xs text-muted py-4 text-center">{t("list.loading")}</div>
          ) : runs.length === 0 ? (
            <div className="text-xs text-muted py-4 text-center">{t("list.empty")}</div>
          ) : (
            <div className="divide-y divide-border">
              {runs.map((run) => (
                <button
                  key={run.run_id}
                  onClick={() => onSelect(run)}
                  className={cn(
                    "w-full flex items-center gap-3 py-2.5 px-2 text-left rounded transition-colors",
                    activeRun?.run_id === run.run_id
                      ? "bg-accent/10"
                      : "hover:bg-accent/5",
                  )}
                >
                  <StatusDot status={statusDot(run)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-heading truncate">{run.name}</span>
                      <Badge variant="neutral">{run.run_id}</Badge>
                      {run.parent_run_id ? (
                        <Badge variant="info">↩ {run.parent_run_id}</Badge>
                      ) : null}
                    </div>
                    <div className="text-[11px] text-muted">
                      {t(`status.${run.status}`)}
                      {run.status === "stopped" && run.stop_reason
                        ? ` · ${t(`stopReason.${run.stop_reason}`)}`
                        : ""}
                      {" · "}
                      {run.running ? t("list.elapsed", { sec: elapsed(run) }) : timeAgo(run)}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="space-y-3">
        {activeRun ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <StatusDot status={statusDot(activeRun)} />
                <span className="font-medium text-heading truncate">
                  {activeRun.name}
                </span>
                <Badge variant="neutral">{activeRun.run_id}</Badge>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {activeRun.running ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    disabled={stopMutation.isPending}
                    onClick={() => stopMutation.mutate(activeRun.run_id)}
                  >
                    {stopMutation.isPending ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Square size={13} />
                    )}
                    {t("action.stop")}
                  </Button>
                ) : activeRun.status === "stopped" ? (
                  <Button
                    variant="primary"
                    size="sm"
                    disabled={resumeMutation.isPending}
                    onClick={() => resumeMutation.mutate(activeRun.run_id)}
                  >
                    {resumeMutation.isPending ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <RotateCcw size={13} />
                    )}
                    {t("action.resume")}
                  </Button>
                ) : (
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setResumeOf(activeRun.run_id);
                      toast.info(t("toast.revisionHint", { id: activeRun.run_id }));
                    }}
                  >
                    <Play size={13} />
                    {t("action.revise")}
                  </Button>
                )}
              </div>
            </div>
            {detail ? (
              <RunDetail detail={detail} />
            ) : (
              <div className="text-xs text-muted py-8 text-center">
                <Loader2 size={16} className="animate-spin inline mr-2" />
                {t("detail.loading")}
              </div>
            )}
          </>
        ) : (
          <div className="rounded-lg border border-dashed border-border py-16 text-center text-sm text-muted">
            {t("detail.empty")}
          </div>
        )}
      </div>
    </div>
  );
}

function statusDot(run: WorkflowRun): "ok" | "warn" | "offline" {
  if (run.running || run.status === "completed") return "ok";
  if (run.status === "running") return "warn";
  return "offline";
}

function elapsed(run: WorkflowRun): number {
  const start = run.created_at ?? 0;
  return start ? Math.max(0, Math.floor(Date.now() / 1000 - start)) : 0;
}

function timeAgo(run: WorkflowRun): string {
  const ts = run.finished_at ?? run.created_at ?? 0;
  if (!ts) return "";
  const diff = Math.floor(Date.now() / 1000 - ts);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export type { WorkflowRunDetail };
