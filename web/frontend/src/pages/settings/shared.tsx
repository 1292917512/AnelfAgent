import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** 键值信息行（系统信息类面板通用） */
export function InfoRow({ label, value, mono = true, className }: {
  label: ReactNode;
  value: ReactNode;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-2 p-2.5 rounded-md bg-elevated border border-border", className)}>
      <span className="text-xs text-muted shrink-0">{label}</span>
      <span className={cn("text-sm text-heading truncate", mono && "font-mono")}>{value}</span>
    </div>
  );
}
