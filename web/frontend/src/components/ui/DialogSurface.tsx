import { useEffect, useRef, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

export interface DialogSurfaceProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  className?: string;
  placement?: "center" | "right" | "left" | "top" | "fullscreen";
  dismissible?: boolean;
  onAfterClose?: () => void;
}

const PLACEMENT = {
  center: "bottom-0 left-1/2 -translate-x-1/2 rounded-t-2xl sm:bottom-auto sm:top-1/2 sm:-translate-y-1/2 sm:rounded-2xl max-h-[90dvh] sm:max-h-[85dvh]",
  right: "inset-y-0 right-0 h-dvh border-l",
  left: "inset-y-0 left-0 h-dvh border-r",
  top: "top-[12dvh] left-1/2 -translate-x-1/2 rounded-2xl max-h-[76dvh]",
  fullscreen: "inset-0 h-dvh",
};

export function DialogSurface({
  open, onClose, title, children, className, placement = "center", dismissible = true, onAfterClose,
}: DialogSurfaceProps) {
  const returnFocus = useRef<HTMLElement | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open || typeof CSS === "undefined" || !CSS.supports("selector(select:open)")) return;
    // Radix 在 document 捕获阶段取消 Esc 的默认行为；原生选择器先消费该键才能正常收起。
    const onPickerEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && surface.current?.querySelector("select:open")) event.stopPropagation();
    };
    window.addEventListener("keydown", onPickerEscape, true);
    return () => window.removeEventListener("keydown", onPickerEscape, true);
  }, [open]);
  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next && dismissible) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay fixed inset-0 z-[100] bg-black/45" />
        <Dialog.Content
          ref={surface}
          aria-describedby={undefined}
          data-placement={placement}
          className={cn("dialog-surface fixed z-[100] w-full border border-border bg-card text-foreground shadow-lg outline-none flex flex-col", PLACEMENT[placement], className)}
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            const target = returnFocus.current;
            const closingSurface = '[role="dialog"][data-state="closed"]';
            if (target?.isConnected) {
              // 父子弹层可同时退出；子层不能把焦点拉回正在退出的父层。
              if (!target.closest(closingSurface)) target.focus({ preventScroll: true });
            } else if (document.activeElement === document.body || document.activeElement?.closest(closingSurface)) {
              document.querySelector<HTMLElement>("main")?.focus({ preventScroll: true });
            }
            if (!open) onAfterClose?.();
          }}
          onEscapeKeyDown={(event) => {
            // 关闭由焦点所在的弹层处理，避免快速重开时父层的旧监听吞掉 Esc。
            event.preventDefault();
          }}
          onKeyDown={(event) => {
            if (event.key !== "Escape") return;
            if (event.target instanceof Element && (event.target.closest('[role="menu"]')
              || event.target.closest('[role="dialog"]') !== event.currentTarget)) return;
            event.preventDefault();
            event.stopPropagation();
            if (open && dismissible) onClose();
          }}
          onPointerDownOutside={(event) => { if (!dismissible) event.preventDefault(); }}
        >
          <Dialog.Title className="sr-only">{title}</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
