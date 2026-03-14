import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { SessionSummary } from "@/stores/thinking-store";
import { Clock, Zap, Brain } from "lucide-react";

interface Props {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
}

export function SessionList({ sessions, activeId, onSelect }: Props) {
  const { t } = useTranslation("thinking");

  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-xs text-[var(--muted)] px-4">
        {t("noSessions")}
      </div>
    );
  }

  return (
    <div className="space-y-0.5 p-2">
      {sessions.map((s) => {
        const ts = new Date(s.start_time * 1000);
        const isActive = s.id === activeId;
        return (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            className={cn(
              "w-full text-left px-3 py-2 rounded-[var(--radius-md)] transition-all duration-150",
              "border text-xs",
              isActive
                ? "bg-[var(--accent-subtle)] border-[var(--accent)] text-[var(--text-strong)]"
                : "border-transparent text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)]",
            )}
          >
            <div className="flex items-center gap-1.5 mb-0.5">
              {s.is_introspection ? (
                <Brain size={11} className="text-[var(--ok)] shrink-0" />
              ) : s.is_heartbeat ? (
                <Clock size={11} className="text-[var(--warn)] shrink-0" />
              ) : (
                <Zap size={11} className="text-[var(--accent)] shrink-0" />
              )}
              <span className="font-medium truncate">
                {s.is_introspection ? t("introspection") : s.is_heartbeat ? t("heartbeat") : t("thinkingSession")}
              </span>
              {!s.ended && (
                <span className="ml-auto px-1.5 py-0.5 text-[9px] font-semibold rounded-full bg-[var(--ok-subtle)] text-[var(--ok)]">
                  {t("inProgress")}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 text-[10px] text-[var(--muted)]">
              <span>{ts.toLocaleTimeString()}</span>
              <span>{t("nNodes", { count: s.node_count })}</span>
              {s.duration_ms != null && (
                <span>{(s.duration_ms / 1000).toFixed(1)}s</span>
              )}
            </div>
          </button>
        );
      })}
    </div>
  );
}
