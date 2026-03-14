import { cn } from "@/lib/utils";

interface StatusDotProps {
  status: "ok" | "warn" | "danger" | "offline";
  className?: string;
}

export function StatusDot({ status, className }: StatusDotProps) {
  return (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full",
        status === "ok" && "bg-[var(--ok)] shadow-[0_0_8px_rgba(34,197,94,0.5)]",
        status === "warn" && "bg-[var(--warn)] shadow-[0_0_8px_rgba(245,158,11,0.5)]",
        status === "danger" && "bg-[var(--danger)] shadow-[0_0_8px_rgba(239,68,68,0.5)] animate-[pulse-subtle_2s_ease-in-out_infinite]",
        status === "offline" && "bg-[var(--muted-strong)]",
        className,
      )}
    />
  );
}
