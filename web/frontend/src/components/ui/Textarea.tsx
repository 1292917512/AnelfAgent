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
        "form-control w-full px-3 py-2 resize-y",
        className,
      )}
      {...rest}
    />
  );
});
