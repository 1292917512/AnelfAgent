import { useRef, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { cn } from "@/lib/utils";

export interface DialogSurfaceProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  className?: string;
  placement?: "center" | "right" | "left" | "top";
  dismissible?: boolean;
}

const PLACEMENT = {
  center: "bottom-0 left-1/2 -translate-x-1/2 rounded-t-2xl sm:bottom-auto sm:top-1/2 sm:-translate-y-1/2 sm:rounded-2xl max-h-[90dvh] sm:max-h-[85dvh]",
  right: "inset-y-0 right-0 h-dvh border-l",
  left: "inset-y-0 left-0 h-dvh border-r",
  top: "top-[12dvh] left-1/2 -translate-x-1/2 rounded-2xl max-h-[76dvh]",
};

export function DialogSurface({
  open, onClose, title, children, className, placement = "center", dismissible = true,
}: DialogSurfaceProps) {
  const surfaceRef = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  return (
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next && dismissible) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay fixed inset-0 z-[100] bg-black/45 backdrop-blur-[2px]" />
        <Dialog.Content
          ref={surfaceRef}
          aria-describedby={undefined}
          data-placement={placement}
          className={cn("dialog-surface fixed z-[100] w-full border border-border bg-card text-foreground shadow-lg outline-none flex flex-col", PLACEMENT[placement], className)}
          onOpenAutoFocus={() => {
            returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            if (returnFocus.current?.isConnected) returnFocus.current.focus({ preventScroll: true });
            else document.querySelector<HTMLElement>("main")?.focus({ preventScroll: true });
          }}
          onEscapeKeyDown={(event) => {
            const owner = event.target instanceof Element ? event.target.closest('[role="dialog"]') : null;
            const menu = event.target instanceof Element && event.target.closest('[role="menu"]');
            if (!dismissible || menu || (owner && owner !== surfaceRef.current)) event.preventDefault();
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
