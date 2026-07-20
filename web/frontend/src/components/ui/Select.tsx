import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** 统一下拉选择框（原生 select 封装，自带下拉箭头） */
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select(
  { className, children, ...rest },
  ref,
) {
  return (
    <div className={cn("relative inline-flex items-center", className)}>
      <select
        ref={ref}
        className={cn(
          "w-full h-9 appearance-none rounded-md border border-input bg-elevated pl-3 pr-8 text-sm text-foreground",
          "transition-colors cursor-pointer",
          "focus:outline-none focus:border-ring focus:ring-1 focus:ring-ring",
          "disabled:opacity-50 disabled:cursor-not-allowed",
        )}
        {...rest}
      >
        {children}
      </select>
      <ChevronDown size={14} className="absolute right-2.5 pointer-events-none text-muted" />
    </div>
  );
});
