import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useToastStore, type ToastType } from "@/stores/toast-store";
import { cn } from "@/lib/utils";

const TYPE_STYLE: Record<ToastType, { icon: typeof Info; className: string }> = {
  success: { icon: CheckCircle2, className: "border-l-ok text-ok" },
  error: { icon: XCircle, className: "border-l-danger text-danger" },
  info: { icon: Info, className: "border-l-info text-info" },
};

/** 全局通知渲染器：挂载一次（App 根节点），配合 toast.* 使用 */
export function Toaster() {
  const { t } = useTranslation("common");
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return createPortal(
    <div className="toast-stack fixed z-[200] flex flex-col gap-2">
      {toasts.map((item) => {
        const style = TYPE_STYLE[item.type];
        return (
          <div
            key={item.id}
            role={item.type === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex items-start gap-2.5 rounded-xl border border-border border-l-2 bg-card p-2 shadow-lg animate-rise",
              style.className,
            )}
          >
            <style.icon size={16} className="ml-1 mt-2 shrink-0" aria-hidden="true" />
            <p className="min-w-0 flex-1 py-1.5 text-sm text-foreground wrap-anywhere">
              {item.message}
            </p>
            <button
              type="button" onClick={() => dismiss(item.id)}
              aria-label={t("close")}
              className="grid size-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-hover hover:text-foreground pointer-coarse:size-11"
            >
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>,
    document.body,
  );
}
