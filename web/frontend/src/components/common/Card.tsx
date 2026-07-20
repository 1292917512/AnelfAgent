import { forwardRef } from "react";
import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
  actions?: ReactNode;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card({ title, subtitle, children, className, actions }, ref) {
  return (
    <div
      ref={ref}
      className={cn(
        "rounded-lg border border-border bg-card p-5",
        "shadow-sm transition-all duration-200",
        "hover:border-border-strong hover:shadow-md",
        "animate-rise",
        className,
      )}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between mb-4">
          <div>
            {title && (
              <h3 className="text-[15px] font-semibold tracking-tight text-heading">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-[13px] text-muted mt-1">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
});
