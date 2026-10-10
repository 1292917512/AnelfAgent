import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useMutation } from "@tanstack/react-query";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { QueryError } from "@/components/common/AsyncState";
import { AlertTriangle, CheckCircle2, Info, MessageCircleQuestion, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { uiApi } from "@/lib/api";
import { useWorkbenchStore, type UiAsk, type UiNotification } from "@/stores/workbench-store";
import { Button, Input } from "@/components/ui";

const LEVEL_STYLE: Record<UiNotification["level"], { icon: typeof Info; cls: string }> = {
  info: { icon: Info, cls: "border-border text-info" },
  success: { icon: CheckCircle2, cls: "border-[rgba(34,197,94,0.4)] text-ok" },
  warning: { icon: AlertTriangle, cls: "border-[rgba(245,158,11,0.4)] text-warn" },
  error: { icon: XCircle, cls: "border-[rgba(239,68,68,0.4)] text-danger" },
};

/** AI 通知卡片堆（右上角，可关闭） */
function NotificationStack() {
  const { t } = useTranslation("common");
  const notifications = useWorkbenchStore((s) => s.notifications);
  const dismiss = useWorkbenchStore((s) => s.dismissNotification);

  if (notifications.length === 0) return null;

  return (
    <div className="fixed top-16 right-3 z-50 w-72 max-w-[calc(100vw-24px)] space-y-2">
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
                aria-label={t("close")}
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

/** AI 提问：选项和自由回答共用可重试的提交路径。 */
function AskDialog({ ask }: { ask: UiAsk }) {
  const { t } = useTranslation("workbench");
  const resolveAsk = useWorkbenchStore((state) => state.resolveAsk);
  const [freeText, setFreeText] = useState("");
  const submit = useMutation({
    mutationFn: (value: string) => uiApi.answer(ask.ask_id, value).then((response) => response.data),
    onSuccess: (data) => { if (data.status === "ok") resolveAsk(ask.ask_id); },
  });
  const expired = submit.data?.status === "expired";
  const close = () => { if (expired) resolveAsk(ask.ask_id); else submit.mutate("__skipped__"); };
  return <DialogSurface open onClose={close} dismissible={!submit.isPending} title={ask.question} className="max-w-md p-5">
    <div className="space-y-4 overflow-y-auto">
      <div className="flex items-start gap-2.5">
        <MessageCircleQuestion size={18} className="mt-0.5 shrink-0 text-accent" />
        <p className="whitespace-pre-wrap break-words text-sm font-medium text-heading">{ask.question}</p>
      </div>
      {submit.error && <QueryError compact error={submit.error} />}
      {expired ? <p role="status" className="text-sm text-warn">{t("ask.expired")}</p> : <>
        {ask.options.length > 0 && <div className="flex flex-wrap gap-2">
          {ask.options.map((option, index) => <Button key={`${option}-${index}`} variant="secondary" size="sm" disabled={submit.isPending} onClick={() => submit.mutate(option)}>{option}</Button>)}
        </div>}
        <form onSubmit={(event) => { event.preventDefault(); if (freeText.trim()) submit.mutate(freeText.trim()); }} className="flex items-center gap-2">
          <Input aria-label={t("ask.inputPlaceholder")} value={freeText} onChange={(event) => setFreeText(event.target.value)} placeholder={t("ask.inputPlaceholder")} disabled={submit.isPending} />
          <Button variant="primary" size="sm" type="submit" disabled={!freeText.trim()} loading={submit.isPending}>{t("ask.submit")}</Button>
        </form>
      </>}
      <div className="flex justify-end"><Button variant="ghost" size="sm" disabled={submit.isPending} onClick={close}>{t(expired ? "common:close" : "ask.skip")}</Button></div>
    </div>
  </DialogSurface>;
}

/** 在所有工作台页面展示 AI 通知和提问，按问题标识隔离输入状态。 */
export function UiCommandHost() {
  const navigate = useNavigate();
  useEffect(() => useWorkbenchStore.subscribe((state, previous) => {
    if (state.panelRequestSeq !== previous.panelRequestSeq) void navigate("/");
  }), [navigate]);
  const ask = useWorkbenchStore((state) => state.asks[0]);
  return <><NotificationStack />{ask && <AskDialog key={ask.ask_id} ask={ask} />}</>;
}
