import { useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

export function CancelPlanButton({
  planId,
  chatId,
  onCancel,
}: {
  planId: string;
  chatId: string;
  onCancel: (reason?: string) => void;
}) {
  const { t } = useTranslation("plan");
  const [busy, setBusy] = useState(false);

  const handleCancel = async () => {
    if (busy) return;
    setBusy(true);
    try {
      onCancel(t("status.cancelledByUser"));
      try {
        await fetch("/api/chat/cancel-plan", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ chat_id: chatId, plan_id: planId }),
        });
      } catch {
        // 后端接口未实现时静默失败（前端已 optimistic 标记）
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={handleCancel}
      disabled={busy}
      className={cn(
        "px-2 py-1 text-[11px] rounded border transition-colors shrink-0",
        "border-red-400/50 text-red-600 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-950/40",
        busy && "opacity-50 cursor-not-allowed",
      )}
    >
      {busy ? t("panel.cancelling") : t("panel.cancel")}
    </button>
  );
}
