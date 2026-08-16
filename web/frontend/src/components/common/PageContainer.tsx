import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** 统一页面容器：自适应占满主区域 + 间距 + 移动端适配 */
export function PageContainer({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("w-full space-y-4 md:space-y-6", className)}>
      {children}
    </div>
  );
}

/** 统一页面头部：图标 + 标题 + 副标题 + 操作区 */
export function PageHeader({
  icon,
  title,
  subtitle,
  actions,
}: {
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 flex-wrap">
      <div className="min-w-0">
        <h1 className="text-lg md:text-xl font-semibold text-heading flex items-center gap-2">
          {icon}
          <span className="truncate">{title}</span>
        </h1>
        {subtitle && (
          <p className="text-sm text-muted mt-1">{subtitle}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
    </div>
  );
}
