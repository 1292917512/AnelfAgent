/**
 * DelegationsPanel — Dashboard「子代理」全局总览面板。
 *
 * 运行中：全 scope 实时快照（轮次/当前工具/用量/耗时），行内操作
 * 进度（Drawer 轮询进度流）/ 指令（steer/after 双档投递）/ 停止；
 * 最近执行：账本折叠历史，点击查看最终进度日志。
 * 操作反馈经后端既有闭环送达 AI（SteerInbox / 注册表完成通知）。
 */
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleSlash,
  History,
  Loader2,
  ScrollText,
  SendHorizontal,
  XCircle,
  Zap,
} from "lucide-react";
import { apiErrorMessage, delegationApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Drawer } from "@/components/common/Drawer";
import { Button, Modal, Select, Textarea, toast } from "@/components/ui";
import { cn } from "@/lib/utils";
import type { DelegationHistoryItem, DelegationOverviewItem } from "@/lib/types";

/** scope（user_{adapter}:{uid}）→ 频道 adapter 徽标文本 */
function scopeAdapter(scope: string): string {
  const m = scope.match(/^[a-z]+_([^:]+):/);
  return m?.[1] ?? "";
}

function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

type StatusTone = "ok" | "danger" | "muted" | "warn";

function historyStatusTone(status: string): StatusTone {
  if (status === "成功") return "ok";
  if (status === "失败") return "danger";
  if (status === "已取消") return "muted";
  return "warn"; // lost（进程中断）等
}

function historyStatusKey(status: string): string {
  if (status === "成功") return "statusSuccess";
  if (status === "失败") return "statusFailed";
  if (status === "已取消") return "statusCancelled";
  return "statusLost";
}

const TONE_CLASSES: Record<StatusTone, string> = {
  ok: "bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-300",
  danger: "bg-red-100 dark:bg-red-900/40 text-red-600 dark:text-red-300",
  muted: "bg-muted text-muted",
  warn: "bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-300",
};

function StatusChip({ tone, label }: { tone: StatusTone; label: string }) {
  const Icon = tone === "ok" ? CheckCircle2 : tone === "danger" ? XCircle : tone === "warn" ? AlertTriangle : CircleSlash;
  return (
    <span className={cn("inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px]", TONE_CLASSES[tone])}>
      <Icon size={10} />
      {label}
    </span>
  );
}

/** 进度流 Drawer：轮询尾部日志，自动吸附底部 */
function ProgressDrawer({
  delegationId,
  title,
  onClose,
}: {
  delegationId: string;
  title: string;
  onClose: () => void;
}) {
  const { t } = useTranslation("plan");
  const { data } = useQuery({
    queryKey: ["delegations", "progress", delegationId],
    queryFn: () => delegationApi.progress(delegationId).then((r) => r.data),
    refetchInterval: 2000,
  });
  const bottomRef = useRef<HTMLDivElement>(null);
  const lineCount = data?.lines.length ?? 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [lineCount, delegationId]);

  return (
    <Drawer open onClose={onClose} title={title} width="max-w-xl">
      <div className="flex items-center gap-2 mb-3 text-[11px] text-muted">
        {data?.running ? (
          <span className="inline-flex items-center gap-1 text-blue-500">
            <Loader2 size={11} className="animate-spin" />
            {t("delegation.running")}
          </span>
        ) : (
          <span>{t("delegation.completed")}</span>
        )}
        {data?.truncated && <span>{t("delegation.panel.truncated")}</span>}
      </div>
      {lineCount > 0 ? (
        <div className="max-h-[70dvh] overflow-y-auto rounded-md bg-elevated border border-border p-3 font-mono text-[11px] leading-relaxed text-foreground whitespace-pre-wrap break-words">
          {data?.lines.map((line, i) => <div key={i}>{line}</div>)}
          <div ref={bottomRef} />
        </div>
      ) : (
        <p className="text-muted text-sm py-6 text-center">{t("delegation.panel.progressEmpty")}</p>
      )}
    </Drawer>
  );
}

function RunningRow({
  item,
  extraSeconds,
  onShowProgress,
  onSteer,
  onCancel,
  cancelling,
}: {
  item: DelegationOverviewItem;
  /** 距上次数据刷新的本地漂移秒数（耗时平滑跳动） */
  extraSeconds: number;
  onShowProgress: () => void;
  onSteer: () => void;
  onCancel: () => void;
  cancelling: boolean;
}) {
  const { t } = useTranslation("plan");
  const adapter = scopeAdapter(item.scope);
  const usage = item.usage ?? {};
  const totalTokens = (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);

  return (
    <div className="rounded-md border border-blue-400/40 bg-blue-50/30 dark:bg-blue-950/20 px-3 py-2">
      <div className="flex items-center gap-2 min-w-0">
        <Bot size={14} className="shrink-0 text-blue-500" />
        <span className="text-sm font-medium text-foreground flex-1 min-w-0 truncate">
          {item.goal || t("delegation.untitled")}
        </span>
        <span className="text-xs text-muted shrink-0 font-mono">
          {formatDuration(item.elapsed_seconds + extraSeconds)}
        </span>
        {item.background && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300 shrink-0">
            {t("delegation.background")}
          </span>
        )}
        {item.role === "orchestrator" && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300 shrink-0">
            <Zap size={9} className="inline -mt-0.5 mr-0.5" />
            {t("delegation.orchestrator")}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1.5 text-[11px] text-muted flex-wrap">
        <Loader2 size={11} className="animate-spin shrink-0 text-blue-500" />
        <span className="truncate">
          {item.current_tool
            ? t("delegation.progress.usingTool", { tool: item.current_tool })
            : item.iteration > 0
              ? t("delegation.progress.round", { n: item.iteration })
              : t("delegation.running")}
        </span>
        {item.agent && <span className="text-accent truncate">@{item.agent}</span>}
        {item.model && <span className="truncate">{item.model}</span>}
        {adapter && (
          <span className="px-1.5 py-0.5 rounded bg-accent-subtle text-accent text-[10px]">{adapter}</span>
        )}
        {(usage.turns ?? 0) > 0 && (
          <span>{t("delegation.panel.turns", { n: usage.turns })}</span>
        )}
        {totalTokens > 0 && (
          <span>{t("delegation.panel.tokens", { n: formatTokens(totalTokens) })}</span>
        )}
        <span className="flex-1" />
        <button
          onClick={onShowProgress}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          <ScrollText size={11} />
          {t("delegation.panel.progress")}
        </button>
        <button
          onClick={onSteer}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-accent hover:bg-accent-subtle transition-colors"
        >
          <SendHorizontal size={11} />
          {t("delegation.panel.steer")}
        </button>
        <button
          onClick={onCancel}
          disabled={cancelling}
          className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-danger hover:bg-danger-subtle transition-colors disabled:opacity-50"
        >
          <CircleSlash size={11} />
          {cancelling ? t("delegation.cancelling") : t("delegation.panel.stop")}
        </button>
      </div>
    </div>
  );
}

function HistoryRow({ item, onShowProgress }: { item: DelegationHistoryItem; onShowProgress: () => void }) {
  const { t } = useTranslation("plan");
  const tone = historyStatusTone(item.status);
  const adapter = item.adapter_key || scopeAdapter(item.scope);
  return (
    <button
      onClick={onShowProgress}
      className="w-full flex items-center gap-2 px-3 py-1.5 rounded-sm bg-elevated border border-border hover:bg-hover transition-colors text-left"
    >
      <StatusChip tone={tone} label={t(`delegation.panel.${historyStatusKey(item.status)}`)} />
      <span className="text-xs text-foreground flex-1 min-w-0 truncate">{item.goal || t("delegation.untitled")}</span>
      {item.agent && <span className="text-[10px] text-accent truncate">@{item.agent}</span>}
      {adapter && <span className="text-[10px] px-1 py-0.5 rounded bg-accent-subtle text-accent">{adapter}</span>}
      <span className="text-[10px] text-muted font-mono shrink-0">{formatDuration(item.duration_seconds)}</span>
      <span className="text-[10px] text-muted shrink-0">
        {new Date(item.finished_at * 1000).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}
      </span>
    </button>
  );
}

export function DelegationsPanel() {
  const { t } = useTranslation("plan");
  const queryClient = useQueryClient();
  const [progressTarget, setProgressTarget] = useState<{ id: string; title: string } | null>(null);
  const [steerTarget, setSteerTarget] = useState<DelegationOverviewItem | null>(null);
  const [steerMessage, setSteerMessage] = useState("");
  const [steerMode, setSteerMode] = useState<"steer" | "after">("steer");
  const [cancellingIds, setCancellingIds] = useState<Set<string>>(new Set());

  const { data: overview, dataUpdatedAt } = useQuery({
    queryKey: ["delegations", "overview"],
    queryFn: () => delegationApi.overview().then((r) => r.data),
    refetchInterval: 3000,
  });
  const { data: history } = useQuery({
    queryKey: ["delegations", "history"],
    queryFn: () => delegationApi.history().then((r) => r.data),
    refetchInterval: 10000,
  });

  const running = overview?.running ?? [];
  const historyItems = history?.items ?? [];

  // 运行中时每秒钟刷新耗时显示（数据本身 3s 轮询，漂移量本地补齐）
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (running.length === 0) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running.length]);
  const extraSeconds = running.length > 0 ? Math.max(0, (now - dataUpdatedAt) / 1000) : 0;

  // 已停止的委托从"取消中"集合清理
  useEffect(() => {
    setCancellingIds((prev) => {
      if (prev.size === 0) return prev;
      const alive = new Set(running.map((r) => r.delegation_id));
      const next = new Set([...prev].filter((id) => alive.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [running]);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["delegations"] });

  const cancelMutation = useMutation({
    mutationFn: (delegationId: string) => delegationApi.cancel(delegationId),
    onSuccess: (r, delegationId) => {
      if (r.data.status !== "ok") {
        setCancellingIds((prev) => {
          const next = new Set(prev);
          next.delete(delegationId);
          return next;
        });
        toast.error(r.data.error || t("delegation.panel.opFailed"));
        return;
      }
      invalidate();
    },
    onError: (err, delegationId) => {
      setCancellingIds((prev) => {
        const next = new Set(prev);
        next.delete(delegationId);
        return next;
      });
      toast.error(apiErrorMessage(err, t("delegation.panel.opFailed")));
    },
  });

  const steerMutation = useMutation({
    mutationFn: () => delegationApi.steer(steerTarget!.delegation_id, steerMessage.trim(), steerMode),
    onSuccess: (r) => {
      if (r.data.status !== "ok") {
        toast.error(r.data.error || t("delegation.panel.opFailed"));
        return;
      }
      toast.success(t("delegation.panel.steerSent"));
      setSteerTarget(null);
      setSteerMessage("");
      setSteerMode("steer");
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("delegation.panel.opFailed"))),
  });

  const handleCancel = (delegationId: string) => {
    setCancellingIds((prev) => new Set(prev).add(delegationId));
    cancelMutation.mutate(delegationId);
  };

  return (
    <Card
      title={t("delegation.panel.title")}
      actions={
        running.length > 0 ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-blue-500">
            <Loader2 size={12} className="animate-spin" />
            {t("activity.runningCount", { n: running.length })}
          </span>
        ) : undefined
      }
    >
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted mb-2">
            <Bot size={12} />
            {t("delegation.panel.running")}
          </div>
          {running.length > 0 ? (
            <div className="space-y-2 max-h-[320px] overflow-y-auto">
              {running.map((item) => (
                <RunningRow
                  key={item.delegation_id}
                  item={item}
                  extraSeconds={extraSeconds}
                  cancelling={cancellingIds.has(item.delegation_id)}
                  onShowProgress={() => setProgressTarget({ id: item.delegation_id, title: item.goal || t("delegation.untitled") })}
                  onSteer={() => setSteerTarget(item)}
                  onCancel={() => handleCancel(item.delegation_id)}
                />
              ))}
            </div>
          ) : (
            <p className="text-muted text-sm py-3">{t("delegation.panel.empty")}</p>
          )}
        </div>

        <div>
          <div className="flex items-center gap-1.5 text-xs font-medium text-muted mb-2">
            <History size={12} />
            {t("delegation.panel.history")}
          </div>
          {historyItems.length > 0 ? (
            <div className="space-y-1.5 max-h-[320px] overflow-y-auto">
              {historyItems.map((item) => (
                <HistoryRow
                  key={item.delegation_id}
                  item={item}
                  onShowProgress={() => setProgressTarget({ id: item.delegation_id, title: item.goal || t("delegation.untitled") })}
                />
              ))}
            </div>
          ) : (
            <p className="text-muted text-sm py-3">{t("delegation.panel.noHistory")}</p>
          )}
        </div>
      </div>

      {progressTarget && (
        <ProgressDrawer
          delegationId={progressTarget.id}
          title={progressTarget.title}
          onClose={() => setProgressTarget(null)}
        />
      )}

      <Modal
        open={steerTarget !== null}
        onClose={() => setSteerTarget(null)}
        title={t("delegation.panel.steerTitle")}
        footer={
          <>
            <Button variant="ghost" onClick={() => setSteerTarget(null)}>
              {t("panel.cancel")}
            </Button>
            <Button
              variant="primary"
              loading={steerMutation.isPending}
              disabled={!steerMessage.trim()}
              onClick={() => steerMutation.mutate()}
            >
              {t("delegation.panel.steerSubmit")}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <p className="text-xs text-muted truncate">{steerTarget?.goal}</p>
          <Select value={steerMode} onChange={(e) => setSteerMode(e.target.value as "steer" | "after")} className="w-full">
            <option value="steer">{t("delegation.panel.steerModeSteer")}</option>
            <option value="after">{t("delegation.panel.steerModeAfter")}</option>
          </Select>
          <Textarea
            value={steerMessage}
            onChange={(e) => setSteerMessage(e.target.value)}
            placeholder={t("delegation.panel.steerPlaceholder")}
            rows={4}
          />
        </div>
      </Modal>
    </Card>
  );
}
