import { useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, Info, MessageCircleQuestion, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { uiApi } from "@/lib/api";
import { useWorkbenchStore, type UiNotification } from "@/stores/workbench-store";
import { Button, Input } from "@/components/ui";

const LEVEL_STYLE: Record<UiNotification["level"], { icon: typeof Info; cls: string }> = {
  info: { icon: Info, cls: "border-border text-info" },
  success: { icon: CheckCircle2, cls: "border-[rgba(34,197,94,0.4)] text-ok" },
  warning: { icon: AlertTriangle, cls: "border-[rgba(245,158,11,0.4)] text-warn" },
  error: { icon: XCircle, cls: "border-[rgba(239,68,68,0.4)] text-danger" },
};

/** AI 通知卡片堆（右上角，可关闭） */
function NotificationStack() {
  const notifications = useWorkbenchStore((s) => s.notifications);
  const dismiss = useWorkbenchStore((s) => s.dismissNotification);

  if (notifications.length === 0) return null;

  return (
    <div className="absolute top-3 right-3 z-30 w-72 space-y-2">
      {notifications.slice(0, 5).map((n) => {
        const { icon: Icon, cls } = LEVEL_STYLE[n.level] ?? LEVEL_STYLE.info;
        return (
          <div
            key={n.id}
            className={cn("rounded-md border bg-card shadow-md px-3 py-2 animate-slide-in-right", cls)}
          >
            <div className="flex items-start gap-2">
              <Icon size={14} className="mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-heading truncate">{n.title}</div>
                {n.content && (
                  <div className="text-[11px] text-muted mt-0.5 break-words line-clamp-3">{n.content}</div>
                )}
              </div>
              <button
                onClick={() => dismiss(n.id)}
                className="p-0.5 rounded text-muted hover:text-foreground transition-colors shrink-0"
              >
                <X size={12} />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** AI 弹窗提问（ui_ask）：选项按钮或自由输入 */
function AskDialog() {
  const { t } = useTranslation("workbench");
  const asks = useWorkbenchStore((s) => s.asks);
  const resolveAsk = useWorkbenchStore((s) => s.resolveAsk);
  const [freeText, setFreeText] = useState("");

  const ask = asks[0];
  if (!ask) return null;

  const answer = (value: string) => {
    uiApi.answer(ask.ask_id, value).catch(() => { /* 过期忽略 */ });
    resolveAsk(ask.ask_id);
    setFreeText("");
  };

  return createPortal(
    <div className="fixed inset-0 z-[110] bg-black/50 flex items-center justify-center p-4 animate-fade-in">
      <div className="w-full max-w-md rounded-lg border border-border bg-card shadow-xl p-5 space-y-4">
        <div className="flex items-start gap-2.5">
          <MessageCircleQuestion size={18} className="text-accent mt-0.5 shrink-0" />
          <div className="text-sm text-heading whitespace-pre-wrap break-words">{ask.question}</div>
        </div>

        {ask.options.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {ask.options.map((opt) => (
              <Button key={opt} variant="secondary" size="sm" onClick={() => answer(opt)}>
                {opt}
              </Button>
            ))}
          </div>
        ) : (
          <form
            onSubmit={(e) => { e.preventDefault(); answer(freeText.trim() || " "); }}
            className="flex items-center gap-2"
          >
            <Input
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              placeholder={t("ask.inputPlaceholder")}
              autoFocus
            />
            <Button variant="primary" size="sm" type="submit">
              {t("ask.submit")}
            </Button>
          </form>
        )}

        <div className="flex justify-end">
          <button
            onClick={() => answer("__skipped__")}
            className="text-[11px] text-muted hover:text-foreground transition-colors"
          >
            {t("ask.skip")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** UI 命令宿主：渲染 AI 投递的通知与提问 */
export function UiCommandHost() {
  return (
    <>
      <NotificationStack />
      <AskDialog />
    </>
  );
}
