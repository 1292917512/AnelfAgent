import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/** 统一加载指示器 */
export function Spinner({ size = 18, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={cn("animate-spin text-muted", className)} />;
}

/** 居中加载区块（页面/面板级） */
export function LoadingBlock({ label, className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 py-10 text-muted", className)}>
      <Spinner size={22} />
      {label && <span className="text-xs">{label}</span>}
    </div>
  );
}
