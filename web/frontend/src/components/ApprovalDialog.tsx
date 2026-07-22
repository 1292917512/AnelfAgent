/**
 * 审批弹窗 — SSE 驱动的全局权限批准对话框。
 *
 * 对齐 Claude Code 权限对话框的三档决策：
 * - 允许一次
 * - 本会话不再询问（会话级放行规则）
 * - 永久允许（写入权限规则文件）
 * - 拒绝（可附言）
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { ShieldAlert, Check, X, Timer, Repeat, Infinity as InfinityIcon } from "lucide-react";
import { approvalsApi } from "@/lib/api";
import { useApprovalPopupStore } from "@/stores/approval-popup-store";
import { cn } from "@/lib/utils";

const RISK_STYLE: Record<string, string> = {
  low: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  medium: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  critical: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

export function ApprovalDialog() {
  const { t } = useTranslation("approvals");
  const queue = useApprovalPopupStore((s) => s.queue);
  const dismiss = useApprovalPopupStore((s) => s.dismiss);
  const current = queue[0];

  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);

  // 倒计时（超时后端会按规则 on_timeout 处理）
  useEffect(() => {
    if (!current) return;
    const deadline = current.received_at + current.timeout_seconds * 1000;
    const tick = () => setRemaining(Math.max(0, Math.round((deadline - Date.now()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [current]);

  useEffect(() => {
    setReason("");
    setBusy(false);
  }, [current?.request_id]);

  if (!current) return null;

  const decide = async (action: "approve" | "deny", remember: string = "once") => {
    setBusy(true);
    try {
      if (action === "approve") {
        await approvalsApi.approve(current.request_id, reason, remember);
      } else {
        await approvalsApi.deny(current.request_id, reason);
      }
    } catch { /* 请求可能已被其他端决策 */ }
    dismiss(current.request_id);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-[480px] max-w-[92vw] rounded-lg border border-border bg-card shadow-xl">
        {/* 头部 */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <ShieldAlert className="h-5 w-5 text-orange-500" />
          <div className="flex-1 font-medium text-heading">{t("popup.title")}</div>
          <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", RISK_STYLE[current.risk_level] ?? RISK_STYLE.medium)}>
            {t(`risk.${current.risk_level}`)}
          </span>
          <span className={cn("flex items-center gap-1 text-xs", remaining <= 10 ? "text-red-500" : "text-muted")}>
            <Timer className="h-3.5 w-3.5" />
            {remaining}s
          </span>
        </div>

        {/* 正文 */}
        <div className="space-y-3 px-4 py-3">
          <div>
            <div className="text-xs text-muted mb-1">{t("popup.tool")}</div>
            <div className="font-mono text-sm text-foreground">{current.tool_name}</div>
          </div>
          {current.tool_args && (
            <div>
              <div className="text-xs text-muted mb-1">{t("popup.args")}</div>
              <pre className="max-h-40 overflow-auto rounded bg-muted p-2 text-xs text-foreground whitespace-pre-wrap break-all">
                {current.tool_args}
              </pre>
            </div>
          )}
          {current.reason && (
            <div className="text-xs text-muted">{current.reason}</div>
          )}
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={t("popup.feedbackPlaceholder")}
            className="w-full rounded border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
          />
        </div>

        {/* 决策按钮 */}
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3">
          <div className="flex gap-2">
            <button
              onClick={() => decide("approve", "once")}
              disabled={busy}
              className="flex-1 flex items-center justify-center gap-1.5 rounded bg-primary px-3 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              {t("popup.allowOnce")}
            </button>
            <button
              onClick={() => decide("deny")}
              disabled={busy}
              className="flex items-center justify-center gap-1.5 rounded bg-destructive px-3 py-2 text-sm text-destructive-foreground hover:bg-destructive/90 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
              {t("popup.deny")}
            </button>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => decide("approve", "session")}
              disabled={busy}
              className="flex-1 flex items-center justify-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted disabled:opacity-50"
            >
              <Repeat className="h-3.5 w-3.5" />
              {t("popup.allowSession")}
            </button>
            <button
              onClick={() => decide("approve", "always")}
              disabled={busy}
              className="flex-1 flex items-center justify-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted disabled:opacity-50"
            >
              <InfinityIcon className="h-3.5 w-3.5" />
              {t("popup.allowAlways")}
            </button>
          </div>
          {queue.length > 1 && (
            <div className="text-center text-xs text-muted">
              {t("popup.more", { count: queue.length - 1 })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
