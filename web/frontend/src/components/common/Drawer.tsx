import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "../ui/Button";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** 面板最大宽度，默认 max-w-md */
  width?: string;
}

/** 统一右侧滑出抽屉：遮罩关闭 / ESC 关闭 / 滚动锁定，移动端近全宽 */
export function Drawer({ open, onClose, title, children, footer, width = "max-w-md" }: DrawerProps) {
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
    <div className="fixed inset-0 z-[100] bg-black/50 animate-fade-in" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          "absolute inset-y-0 right-0 w-full bg-card border-l border-border shadow-lg",
          "flex flex-col animate-slide-in-right",
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
        <div className="flex-1 px-5 py-4 overflow-y-auto">{children}</div>
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
