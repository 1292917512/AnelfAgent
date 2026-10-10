import { useState } from "react";
import { useTranslation } from "react-i18next";
import { RefreshCw, Search, X } from "lucide-react";
import type { SessionSummary } from "@/lib/types";
import { Button } from "@/components/ui/Button";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { QueryError } from "@/components/common/AsyncState";
import { SessionList } from "./SessionList";
import { sessionKind } from "./trace-model";

interface Props {
  sessions: SessionSummary[]; activeId: string | null; onSelect: (id: string) => void;
  onRefresh: () => void; loading: boolean; error: unknown;
  isMobile: boolean; open: boolean; onClose: () => void;
}

export function ThinkingSessionsPanel({ sessions, activeId, onSelect, onRefresh, loading, error, isMobile, open, onClose }: Props) {
  const { t } = useTranslation("thinking");
  const { t: tc } = useTranslation("common");
  const [search, setSearch] = useState("");
  const filtered = sessions.filter((session) => [
    session.id, session.label, session.scope, t(sessionKind(session)), new Date(session.start_time * 1000).toLocaleString(),
  ].join(" ").toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const content = <>
    <div className="space-y-3 border-b border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-heading">{t("sessionList")} <span className="text-xs font-normal text-muted">{sessions.length}</span></h2>
        <div className="flex">
          <Button size="icon" variant="ghost" loading={loading} title={t("refresh")} onClick={onRefresh}><RefreshCw size={14} /></Button>
          {isMobile && <Button size="icon" variant="ghost" title={tc("close")} onClick={onClose}><X size={16} /></Button>}
        </div>
      </div>
      <div className="relative">
        <Search size={13} className="absolute left-2.5 top-2.5 text-muted" />
        <input value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("searchSessions")} placeholder={t("searchSessions")}
          className="h-8 w-full rounded-lg border border-input bg-card pl-8 pr-2 text-xs outline-none focus:border-accent" />
      </div>
      <p className="text-[11px] leading-4 text-muted">{t("retentionHint")}</p>
    </div>
    {error != null && <div className="p-2"><QueryError compact error={error} retry={onRefresh} /></div>}
    <div className="min-h-0 flex-1 overflow-y-auto">
      <SessionList sessions={filtered} activeId={activeId} onSelect={(id) => { onSelect(id); if (isMobile) onClose(); }} />
    </div>
  </>;
  return isMobile
    ? <DialogSurface open={open} onClose={onClose} title={t("sessionList")} placement="left" className="sm:max-w-sm">{content}</DialogSurface>
    : <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-panel">{content}</aside>;
}
