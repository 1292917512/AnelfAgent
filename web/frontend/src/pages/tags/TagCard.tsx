import type { TOptions } from "i18next";
import { Lock, MessageSquare, Trash2, Wrench } from "lucide-react";
import { cn } from "@/lib/utils";
import type { UnifiedTag } from "@/lib/types";

function SourceBadge({ source, t }: { source: "message" | "tool"; t: (k: string) => string }) {
  if (source === "message") {
    return (
      <span
        title={t("sourceMessageTooltip")}
        className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
          bg-accent-subtle text-info border border-info/20 cursor-help"
      >
        <MessageSquare size={8} />
        {t("sourceMessage")}
      </span>
    );
  }
  return (
    <span
      title={t("sourceToolTooltip")}
      className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
        bg-secondary text-muted border border-border cursor-help"
    >
      <Wrench size={8} />
      {t("sourceTool")}
    </span>
  );
}

export function TagCard({
  tag,
  onDelete,
  t,
}: {
  tag: UnifiedTag;
  onDelete?: () => void;
  t: (k: string, opts?: TOptions) => string;
}) {
  return (
    <div
      className={cn(
        "group flex flex-col gap-1.5 p-3 rounded-md border transition-colors",
        tag.builtin
          ? "border-border bg-secondary hover:border-border-strong"
          : "border-accent/30 bg-accent/5 hover:border-accent/50",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <code className="text-xs font-mono font-semibold text-heading">
              [{tag.name}]
            </code>
            {tag.builtin ? (
              <span
                title={t("builtinTooltip")}
                className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded-full
                  bg-secondary text-muted border border-border cursor-help"
              >
                <Lock size={8} />
                {t("builtin")}
              </span>
            ) : (
              <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-accent/10 text-accent border border-accent/20">
                {t("custom")}
              </span>
            )}
            {tag.sources
              .filter((s) => s !== "custom")
              .map((s) => (
                <SourceBadge key={s} source={s as "message" | "tool"} t={t} />
              ))}
          </div>
          {tag.description ? (
            <p className="text-[11px] text-muted mt-1 leading-relaxed">
              {tag.description}
            </p>
          ) : (
            <p className="text-[11px] text-muted/50 mt-1 italic">—</p>
          )}
        </div>
        {/* 删除按钮（触屏常显） */}
        {!tag.builtin && onDelete && (
          <button
            onClick={onDelete}
            className="opacity-100 md:opacity-0 md:group-hover:opacity-100 flex-shrink-0 p-1 rounded
              text-muted hover:text-danger hover:bg-danger/10 transition-all"
            title={t("common:delete")}
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  );
}
