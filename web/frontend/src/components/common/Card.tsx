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
        "rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--card)] p-5",
        "shadow-[var(--shadow-sm)] transition-all duration-[var(--duration-normal)]",
        "hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-md)]",
        "animate-[rise_0.35s_var(--ease-out)_backwards]",
        className,
      )}
    >
      {(title || actions) && (
        <div className="flex items-center justify-between mb-4">
          <div>
            {title && (
              <h3 className="text-[15px] font-semibold tracking-tight text-[var(--text-strong)]">
                {title}
              </h3>
            )}
            {subtitle && (
              <p className="text-[13px] text-[var(--muted)] mt-1">{subtitle}</p>
            )}
          </div>
          {actions && <div className="flex gap-2">{actions}</div>}
        </div>
      )}
      {children}
    </div>
  );
});
