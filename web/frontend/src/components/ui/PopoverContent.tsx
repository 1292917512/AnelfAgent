import { forwardRef, type ComponentPropsWithoutRef, type ElementRef } from "react";
import * as Popover from "@radix-ui/react-popover";
import { cn } from "@/lib/utils";

/** 共享浮层外壳：视口避让、滚动边界与进退动画；业务保留自身内容和焦点策略。 */
export const PopoverContent = forwardRef<ElementRef<typeof Popover.Content>, ComponentPropsWithoutRef<typeof Popover.Content>>(
  function PopoverContent({ className, sideOffset = 8, collisionPadding = 12, ...props }, ref) {
    return <Popover.Portal>
      <Popover.Content ref={ref} sideOffset={sideOffset} collisionPadding={collisionPadding}
        className={cn("floating-surface z-[120] max-h-[var(--radix-popover-content-available-height)] max-w-[calc(100vw-24px)] overflow-y-auto overscroll-contain rounded-xl border border-border bg-card text-foreground shadow-lg outline-none", className)}
        {...props} />
    </Popover.Portal>;
  },
);
