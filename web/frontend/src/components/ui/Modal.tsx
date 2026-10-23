import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./Button";
import { DialogSurface } from "./DialogSurface";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
  placement?: "center" | "right" | "left";
  dismissible?: boolean;
  onAfterClose?: () => void;
}

export function Modal({
  open, onClose, title, children, footer, width = "max-w-lg", placement = "center", dismissible = true, onAfterClose,
}: ModalProps) {
  const { t } = useTranslation("common");
  // 关闭时宿主可立即清空选中项；保留展示内容直到退出动画和焦点恢复完成。
  const content = [title, children, footer] as const;
  const [lastContent, setLastContent] = useState(content);
  if (open && content.some((value, index) => value !== lastContent[index])) setLastContent(content);
  const [shownTitle, shownChildren, shownFooter] = open ? content : lastContent;
  return (
    <DialogSurface open={open} onClose={onClose} title={shownTitle ?? t("dialog")}
      placement={placement} dismissible={dismissible} className={cn("overflow-hidden", width)} onAfterClose={onAfterClose}>
      <div className="flex items-center gap-3 border-b border-border px-5 py-4 shrink-0">
        <div className="min-w-0 flex-1 text-base font-semibold text-heading">{shownTitle}</div>
        <Button variant="ghost" size="icon" onClick={onClose} disabled={!dismissible} aria-label={t("close")}>
          <X size={18} />
        </Button>
      </div>
      <div className={cn("min-h-0 overflow-y-auto overscroll-contain px-5 py-5", placement !== "center" && "flex-1")}>{shownChildren}</div>
      {shownFooter && <div className="dialog-actions flex flex-wrap items-center justify-end gap-2 border-t border-border bg-elevated/50 px-5 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))] shrink-0">{shownFooter}</div>}
    </DialogSurface>
  );
}

export function ConfirmDialog({
  open, onClose, onConfirm, title, message, confirmText, cancelText, danger = false, loading = false,
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
  const { t } = useTranslation("common");
  return (
    <Modal open={open} onClose={onClose} title={title} width="max-w-md" dismissible={!loading}
      footer={<>
        <Button variant="secondary" disabled={loading} onClick={onClose}>{cancelText ?? t("cancel")}</Button>
        <Button variant={danger ? "danger" : "primary"} loading={loading} onClick={onConfirm}>
          {confirmText ?? t("confirm")}
        </Button>
      </>}>
      {message && <div className="text-sm leading-relaxed text-foreground whitespace-pre-wrap break-words">{message}</div>}
    </Modal>
  );
}
