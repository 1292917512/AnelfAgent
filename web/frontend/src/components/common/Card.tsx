import { forwardRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface CardProps {
  title?: string;
  subtitle?: string;
  children: ReactNode;
  className?: string;
  actions?: ReactNode;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card({ title, subtitle, children, className, actions }, ref) {
  return (
    <section ref={ref} className={cn("surface-card rounded-xl border border-border bg-card p-5 shadow-sm", className)}>
      {(title || actions) && <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          {title && <h2 className="text-sm font-semibold text-heading">{title}</h2>}
          {subtitle && <p className="mt-1 text-xs leading-relaxed text-muted">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>}
      {children}
    </section>
  );
});
