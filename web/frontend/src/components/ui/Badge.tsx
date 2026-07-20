import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

export type BadgeVariant = "neutral" | "accent" | "accent2" | "ok" | "warn" | "danger" | "info";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
}

const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: "bg-secondary text-muted border-border",
  accent: "bg-accent-subtle text-accent border-accent/30",
  accent2: "bg-accent2-subtle text-accent2 border-accent2/30",
  ok: "bg-ok-subtle text-ok border-ok/30",
  warn: "bg-warn-subtle text-warn border-warn/30",
  danger: "bg-danger-subtle text-danger border-danger/30",
  info: "bg-accent-subtle text-info border-info/30",
};

/** 统一状态/能力徽标 */
export function Badge({ variant = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full border text-[10px] font-medium whitespace-nowrap",
        VARIANT_CLASSES[variant],
        className,
      )}
      {...rest}
    />
  );
}
