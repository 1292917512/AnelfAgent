import { useTranslation } from "react-i18next";
import { RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SessionSummary } from "@/lib/types";
import { SessionList } from "./SessionList";

/** 思维链会话列表面板（桌面常驻，移动端抽屉） */
export function ThinkingSessionsPanel({
  sessions,
  activeId,
  onSelect,
  onRefresh,
  isMobile,
  open,
  onClose,
}: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onRefresh: () => void;
  isMobile: boolean;
  open: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation("thinking");
  if (isMobile && !open) return null;
  return (
    <>
      {isMobile && (
        <div className="fixed inset-0 z-40 bg-black/50" onClick={onClose} />
      )}
      <div className={cn(
        "border-r border-border bg-panel flex flex-col",
        isMobile ? "fixed inset-y-0 left-0 z-50 w-64" : "w-56 shrink-0",
      )}>
        <div className="px-4 py-3 border-b border-border">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-heading uppercase tracking-wider">
              {t("sessionList")}
            </span>
            <button
              onClick={onRefresh}
              className="p-1 rounded-sm text-muted hover:text-foreground hover:bg-hover"
              title={t("refresh")}
            >
              <RefreshCw size={12} />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto">
          <SessionList
            sessions={sessions}
            activeId={activeId}
            onSelect={(id) => {
              onSelect(id);
              if (isMobile) onClose();
            }}
          />
        </div>
      </div>
    </>
  );
}
