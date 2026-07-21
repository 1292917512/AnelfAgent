import { createPortal } from "react-dom";
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
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);

  if (toasts.length === 0) return null;

  return createPortal(
    <div className="fixed top-4 right-4 left-4 sm:left-auto z-[200] flex flex-col gap-2 sm:w-[360px]">
      {toasts.map((t) => {
        const style = TYPE_STYLE[t.type];
        return (
          <div
            key={t.id}
            role="alert"
            className={cn(
              "flex items-start gap-2.5 rounded-lg border border-l-2 bg-card px-3.5 py-3 shadow-lg animate-rise",
              style.className,
            )}
          >
            <style.icon size={16} className="mt-0.5 shrink-0" />
            <p className="flex-1 min-w-0 text-sm text-foreground break-words">
              {t.message}
            </p>
            <button
              onClick={() => dismiss(t.id)}
              aria-label="close"
              className="shrink-0 text-muted hover:text-foreground transition-colors"
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
