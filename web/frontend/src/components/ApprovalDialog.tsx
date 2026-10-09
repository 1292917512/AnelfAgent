import { useEffect, useState } from "react";
import { isAxiosError } from "axios";
import { DialogSurface } from "./ui/DialogSurface";
import { useTranslation } from "react-i18next";
import { ShieldAlert, Check, X, Timer, Repeat, Infinity as InfinityIcon } from "lucide-react";
import { approvalsApi } from "@/lib/api";
import { useApprovalPopupStore, type ApprovalRequestPayload } from "@/stores/approval-popup-store";
import { ApprovalPreview } from "./ApprovalPreview";
import { cn } from "@/lib/utils";

const RISK_STYLE: Record<string, string> = {
  low: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300",
  medium: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300",
  high: "bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300",
  critical: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300",
};

export function ApprovalDialog() {
  const queue = useApprovalPopupStore((s) => s.queue);
  const current = queue[0];
  return current ? <ApprovalDecision key={current.request_id} current={current} queued={queue.length - 1} /> : null;
}

function ApprovalDecision({ current, queued }: { current: ApprovalRequestPayload; queued: number }) {
  const { t } = useTranslation("approvals");
  const dismiss = useApprovalPopupStore((s) => s.dismiss);

  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [remaining, setRemaining] = useState(0);

  // 倒计时（超时后端会按规则 on_timeout 处理）
  useEffect(() => {
    const deadline = current.received_at + current.timeout_seconds * 1000;
    const tick = () => setRemaining(Math.max(0, Math.round((deadline - Date.now()) / 1000)));
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [current]);

  const decide = async (action: "approve" | "deny", remember: string = "once") => {
    if (busy) return;
    setBusy(true);
    try {
      if (action === "approve") {
        await approvalsApi.approve(current.request_id, reason, remember);
      } else {
        await approvalsApi.deny(current.request_id, reason);
      }
      dismiss(current.request_id);
    } catch (error) {
      if (isAxiosError(error) && [404, 409].includes(error.response?.status ?? 0)) dismiss(current.request_id);
    } finally {
      setBusy(false);
    }
  };

  return (
    <DialogSurface open title={t("popup.title")} onClose={() => {}} dismissible={false} className="sm:max-w-lg overflow-y-auto">
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
          {current.tool_args && <ApprovalPreview toolName={current.tool_name} toolArgs={current.tool_args} />}
          {current.reason && (
            <div className="text-xs text-muted">{current.reason}</div>
          )}
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            aria-label={t("popup.feedbackPlaceholder")}
            placeholder={t("popup.feedbackPlaceholder")}
            className="w-full rounded border border-border bg-elevated px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-accent"
          />
        </div>

        {/* 决策按钮 */}
        <div className="flex flex-col gap-2 border-t border-border px-4 py-3">
          <div className="flex gap-2">
            <button
              onClick={() => decide("approve", "once")}
              disabled={busy}
              className="flex-1 flex items-center justify-center gap-1.5 rounded bg-accent px-3 py-2 text-sm text-primary-foreground hover:bg-accent/90 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              {t("popup.allowOnce")}
            </button>
            <button
              onClick={() => decide("deny")}
              disabled={busy}
              className="flex items-center justify-center gap-1.5 rounded bg-danger px-3 py-2 text-sm text-white hover:bg-danger/90 disabled:opacity-50"
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
          {queued > 0 && (
            <div className="text-center text-xs text-muted">
              {t("popup.more", { count: queued })}
            </div>
          )}
        </div>
    </DialogSurface>
  );
}
