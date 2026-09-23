import { FileText, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { WorkspaceSearchHit } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 提及候选面板（@ 弹出的文件选择列表，键盘上下导航） */
export function MentionPanel({
  items,
  loading,
  activeIndex,
  onPick,
  onHover,
}: {
  items: WorkspaceSearchHit[];
  loading: boolean;
  activeIndex: number;
  onPick: (hit: WorkspaceSearchHit) => void;
  onHover: (index: number) => void;
}) {
  const { t } = useTranslation("chat");

  return (
    <div
      role="listbox"
      className="absolute bottom-full left-0 mb-1 w-80 max-h-64 overflow-y-auto rounded-lg border border-border bg-popover shadow-lg z-30"
    >
      {loading && items.length === 0 ? (
        <div className="flex items-center gap-2 px-3 py-2.5 text-xs text-muted">
          <Loader2 size={13} className="animate-spin" />
          {t("mention.searching")}
        </div>
      ) : items.length === 0 ? (
        <div className="px-3 py-2.5 text-xs text-muted">{t("mention.empty")}</div>
      ) : (
        items.map((hit, i) => (
          <button
            key={`${hit.path}-${i}`}
            role="option"
            aria-selected={i === activeIndex}
            onMouseDown={(e) => {
              e.preventDefault(); // 不打断输入框焦点
              onPick(hit);
            }}
            onMouseEnter={() => onHover(i)}
            className={cn(
              "flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors",
              i === activeIndex ? "bg-accent/10" : "hover:bg-accent/5",
            )}
          >
            <FileText size={13} className="shrink-0 text-muted" />
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-foreground">
              {hit.path}
            </span>
            {hit.match === "content" && hit.snippet ? (
              <span className="shrink-0 max-w-[40%] truncate text-[10px] text-muted">
                {hit.snippet}
              </span>
            ) : null}
          </button>
        ))
      )}
    </div>
  );
}
