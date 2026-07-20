import { forwardRef, type InputHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** 统一输入框 */
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={cn(
        "w-full h-9 rounded-md border border-input bg-elevated px-3 text-sm text-foreground",
        "placeholder:text-muted transition-colors",
        "focus:outline-none focus:border-ring focus:ring-1 focus:ring-ring",
        "disabled:opacity-50 disabled:cursor-not-allowed",
        className,
      )}
      {...rest}
    />
  );
});
