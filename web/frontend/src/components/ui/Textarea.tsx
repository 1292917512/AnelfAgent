import { forwardRef, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** 统一多行输入框 */
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cn(
        "w-full rounded-md border border-input bg-elevated px-3 py-2 text-sm text-foreground",
        "placeholder:text-muted transition-colors resize-y",
        "focus:outline-none focus:border-ring focus:ring-1 focus:ring-ring",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
      {...rest}
    />
  );
});
