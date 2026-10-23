import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { useWorkbenchStore, type UiAsk, type UiNotification } from "@/stores/workbench-store";
import { AskDialog } from "./AskDialog";

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

/** 在所有工作台页面展示 AI 通知和提问，按问题标识隔离输入状态。 */
export function UiCommandHost() {
  const navigate = useNavigate();
  useEffect(() => useWorkbenchStore.subscribe((state, previous) => {
    if (state.panelRequestSeq !== previous.panelRequestSeq) void navigate("/");
  }), [navigate]);
  const asks = useWorkbenchStore((state) => state.asks);
  const [presented, setPresented] = useState<UiAsk | null>(null);
  useEffect(() => { if (!presented && asks[0]) setPresented(asks[0]); }, [asks, presented]);
  return <><NotificationStack />{presented && <AskDialog key={presented.ask_id} ask={presented}
    active={asks.some((ask) => ask.ask_id === presented.ask_id)} count={asks.length}
    onAfterClose={() => { useWorkbenchStore.getState().resolveAsk(presented.ask_id); setPresented(null); }} />}</>;
}
