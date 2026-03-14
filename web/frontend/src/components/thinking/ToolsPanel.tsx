import { useTranslation } from "react-i18next";
import { Wrench, Package } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  tools: string[];
}

export function ToolsPanel({ tools }: Props) {
  const { t } = useTranslation("thinking");

  if (tools.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-xs text-[var(--muted)] gap-2 px-4">
        <Package size={20} className="opacity-40" />
        <span>{t("waitingToolsLoad")}</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b border-[var(--border)]">
        <div className="flex items-center gap-1.5">
          <Wrench size={12} className="text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--text-strong)] uppercase tracking-wider">
            {t("availableTools")}
          </span>
          <span className="ml-auto text-[10px] font-mono text-[var(--muted)]">
            {tools.length}
          </span>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        <div className="flex flex-wrap gap-1">
          {tools.map((name) => (
            <span
              key={name}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded-full",
                "text-[10px] font-mono leading-relaxed",
                "bg-[var(--accent-subtle)] text-[var(--accent)] border border-[var(--accent)]/20",
              )}
              title={name}
            >
              <Wrench size={9} className="shrink-0 opacity-60" />
              {name}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
