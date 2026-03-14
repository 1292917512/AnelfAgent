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
        "rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)] p-4 transition-all duration-[var(--duration-normal)]",
        "hover:border-[var(--border-strong)] hover:shadow-[var(--shadow-sm)]",
        "animate-[rise_0.35s_var(--ease-out)_backwards]",
        className,
      )}
    >
      <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--muted)]">
        {label}
      </div>
      <div
        className={cn(
          "mt-1.5 text-2xl font-bold tracking-tight leading-tight",
          variant === "ok" && "text-[var(--ok)]",
          variant === "warn" && "text-[var(--warn)]",
          variant === "danger" && "text-[var(--danger)]",
          variant === "default" && "text-[var(--text-strong)]",
        )}
      >
        {value}
      </div>
    </div>
  );
}
