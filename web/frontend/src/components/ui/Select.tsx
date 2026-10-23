import { forwardRef, type SelectHTMLAttributes } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  controlClassName?: string;
}

/** 保留原生表单与键盘语义；支持的浏览器使用主题化选项面板和渐进动效。 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, controlClassName, children, multiple, size, ...rest },
  ref,
) {
  return (
    <div className={cn("app-select-field relative inline-flex min-w-0 max-w-full items-center", className)}>
      <select
        ref={ref}
        multiple={multiple}
        size={size}
        data-picker={!multiple && (!size || size <= 1) ? "true" : undefined}
        className={cn(
          "form-control app-select w-full appearance-none cursor-pointer",
          multiple || (size && size > 1) ? "px-3 py-2" : "h-9 pl-3 pr-8",
          controlClassName,
        )}
        {...rest}
      >
        {children}
      </select>
      {!multiple && (!size || size <= 1) && <ChevronDown size={14} aria-hidden className="select-chevron absolute right-2.5 pointer-events-none text-muted" />}
    </div>
  );
});
