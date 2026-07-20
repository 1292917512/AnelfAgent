import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** 面板最大宽度，默认 max-w-lg */
  width?: string;
}

/** 统一模态框：遮罩关闭 / ESC 关闭 / 滚动锁定，移动端自适应边距 */
export function Modal({ open, onClose, title, children, footer, width = "max-w-lg" }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-black/50 animate-fade-in p-0 sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          "w-full bg-card border border-border shadow-lg animate-rise flex flex-col",
          "max-h-[85dvh] rounded-t-xl sm:rounded-lg",
          width,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {title != null && (
          <div className="flex items-center justify-between px-5 py-4 border-b border-border shrink-0">
            <h3 className="text-[15px] font-semibold text-heading truncate">{title}</h3>
            <Button variant="ghost" size="icon" onClick={onClose} aria-label="close">
              <X size={16} />
            </Button>
          </div>
        )}
        <div className="px-5 py-4 overflow-y-auto">{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-2 px-5 py-4 border-t border-border shrink-0 safe-area-bottom">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** 统一确认对话框 */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  message,
  confirmText,
  cancelText,
  danger = false,
  loading = false,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: ReactNode;
  message?: ReactNode;
  confirmText?: ReactNode;
  cancelText?: ReactNode;
  danger?: boolean;
  loading?: boolean;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      width="max-w-sm"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>
            {cancelText ?? "Cancel"}
          </Button>
          <Button variant={danger ? "danger" : "primary"} size="sm" loading={loading} onClick={onConfirm}>
            {confirmText ?? "OK"}
          </Button>
        </>
      }
    >
      {message && <p className="text-sm text-foreground whitespace-pre-wrap">{message}</p>}
    </Modal>
  );
}
