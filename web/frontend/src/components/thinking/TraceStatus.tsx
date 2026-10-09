import { AlertCircle, CheckCircle2, Clock, Loader2, TriangleAlert } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { TraceNode } from "@/lib/types";
import { cn } from "@/lib/utils";

const STYLES = {
  pending: { icon: Clock, color: "text-muted" },
  running: { icon: Loader2, color: "text-accent" },
  completed: { icon: CheckCircle2, color: "text-ok" },
  error: { icon: AlertCircle, color: "text-danger" },
  warning: { icon: TriangleAlert, color: "text-warn" },
};

export function TraceStatus({ status, label = false }: { status: TraceNode["status"]; label?: boolean }) {
  const { t } = useTranslation("thinking");
  const { icon: Icon, color } = STYLES[status];
  return <span className={cn("inline-flex shrink-0 items-center gap-1.5 text-xs", color)} title={t(`statusLabels.${status}`)}>
    <Icon size={14} className={status === "running" ? "animate-spin" : ""} aria-hidden />
    {label ? t(`statusLabels.${status}`) : <span className="sr-only">{t(`statusLabels.${status}`)}</span>}
  </span>;
}
