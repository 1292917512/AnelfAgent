import type { ReactNode } from "react";
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
}

export function Modal({
  open, onClose, title, children, footer, width = "max-w-lg", placement = "center", dismissible = true,
}: ModalProps) {
  const { t } = useTranslation("common");
  return (
    <DialogSurface open={open} onClose={onClose} title={title ?? t("dialog")}
      placement={placement} dismissible={dismissible} className={width}>
      <div className="flex items-center gap-3 border-b border-border px-5 py-4 shrink-0">
        <div className="min-w-0 flex-1 text-base font-semibold text-heading">{title}</div>
        <Button variant="ghost" size="icon" onClick={onClose} disabled={!dismissible} aria-label={t("close")}>
          <X size={18} />
        </Button>
      </div>
      <div className={cn("min-h-0 overflow-y-auto overscroll-contain px-5 py-5", placement !== "center" && "flex-1")}>{children}</div>
      {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border bg-elevated/50 px-5 py-4 shrink-0 safe-area-bottom">{footer}</div>}
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
      {message && <div className="text-sm leading-relaxed text-foreground whitespace-pre-wrap">{message}</div>}
    </Modal>
  );
}
