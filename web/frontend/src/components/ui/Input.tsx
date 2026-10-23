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
        "form-control w-full h-9 px-3",
        className,
      )}
      {...rest}
    />
  );
});
