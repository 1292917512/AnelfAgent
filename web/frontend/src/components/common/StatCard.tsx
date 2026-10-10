import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface StatCardProps {
  label: string;
  value: ReactNode;
  variant?: "default" | "ok" | "warn" | "danger";
  className?: string;
}

export function StatCard({ label, value, variant = "default", className }: StatCardProps) {
  return (
    <div
      className={cn(
        "min-w-0 rounded-xl border border-border bg-card p-4 sm:p-5",
        className,
      )}
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-muted">
        {label}
      </div>
      <div
        className={cn(
          "mt-2 text-xl font-semibold tracking-tight leading-snug tabular-nums break-words sm:text-2xl",
          variant === "ok" && "text-ok",
          variant === "warn" && "text-warn",
          variant === "danger" && "text-danger",
          variant === "default" && "text-heading",
        )}
      >
        {value}
      </div>
    </div>
  );
}
