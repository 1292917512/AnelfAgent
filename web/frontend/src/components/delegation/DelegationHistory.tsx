import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowUpRight, Check, ChevronRight, GitBranch, History, X } from "lucide-react";
import type { DelegationHistoryItem } from "@/lib/types";
import { Button } from "@/components/ui";
import { formatDuration, scopeAdapter } from "./format";

const STATUS_KEYS = { success: "statusSuccess", failed: "statusFailed", cancelled: "statusCancelled", lost: "statusLost" } as const;

export function DelegationHistory({ items, pending, onSelect }: {
  items: DelegationHistoryItem[]; pending: boolean; onSelect: (item: DelegationHistoryItem) => void;
}) {
  const { t } = useTranslation(["dashboard", "plan"]);
  const [onlyIssues, setOnlyIssues] = useState(false);
  const [visibleCount, setVisibleCount] = useState(6);
  const successes = items.filter((item) => item.status === "success").length;
  const issues = items.filter((item) => item.status === "failed" || item.status === "lost").length;
  const filtered = onlyIssues ? items.filter((item) => item.status === "failed" || item.status === "lost") : items;
  const maxDuration = Math.max(1, ...items.map((item) => item.duration_seconds));
  return <div className="delegation-history">
    <div className="delegation-history-heading">
      <h3><History size={14} />{t("plan:delegation.panel.history")}<span>{items.length}</span></h3>
      <button type="button" aria-pressed={onlyIssues} className="delegation-issue-filter" onClick={() => { setOnlyIssues(!onlyIssues); setVisibleCount(6); }}>{t("history.onlyIssues")}{issues > 0 && <span>{issues}</span>}</button>
    </div>
    {items.length > 0 && <>
      <div className="delegation-history-summary">
        <span><Check size={13} className="text-ok" />{t("history.successCount", { count: successes })}</span>
        <span><X size={13} className={issues > 0 ? "text-danger" : "text-muted"} />{t("history.issueCount", { count: issues })}</span>
        <span>{t("history.otherCount", { count: items.length - successes - issues })}</span>
      </div>
      <div className="delegation-duration-chart" role="group" aria-label={t("history.durationChart")}>
        {[...items].reverse().map((item) => <button type="button" key={item.delegation_id} onClick={() => onSelect(item)}
          aria-label={`${item.goal} · ${formatDuration(item.duration_seconds)} · ${t(`plan:delegation.panel.${STATUS_KEYS[item.status]}`, { defaultValue: item.status })}`}
          title={`${item.goal} · ${formatDuration(item.duration_seconds)}`}>
          <span data-status={item.status} style={{ height: `${Math.max(8, item.duration_seconds / maxDuration * 100)}%` }} />
        </button>)}
      </div>
      <div className="delegation-chart-caption"><span>{t("history.durationChart")}</span><span>{t("history.older")} → {t("history.latest")}</span></div>
    </>}
    <div className="delegation-history-list">
      {filtered.slice(0, visibleCount).map((item) => <button type="button" key={item.delegation_id} onClick={() => onSelect(item)} className="delegation-history-row" data-status={item.status}>
        <span className="delegation-history-marker" aria-hidden="true">{item.status === "success" ? <Check size={13} /> : item.status === "failed" || item.status === "lost" ? <X size={13} /> : <ArrowUpRight size={13} />}</span>
        <span className="delegation-history-body">
          <strong>{item.goal || t("plan:delegation.untitled")}</strong>
          <span className="delegation-history-meta">
            <span className="delegation-result-label">{t(`plan:delegation.panel.${STATUS_KEYS[item.status]}`, { defaultValue: item.status })}</span>
            {(item.agent || item.model) && <span>{item.agent ? `@${item.agent}` : item.model}</span>}
            {(item.adapter_key || scopeAdapter(item.scope)) && <span>{item.adapter_key || scopeAdapter(item.scope)}</span>}
            {item.parent_id && <span title={item.parent_id}><GitBranch size={11} />{t("history.nested", { depth: item.depth ?? 1 })}</span>}
          </span>
          {item.summary && <span className="delegation-result-preview">{item.summary}</span>}
        </span>
        <span className="delegation-history-time"><strong>{formatDuration(item.duration_seconds)}</strong><time dateTime={new Date(item.finished_at * 1000).toISOString()}>{new Date(item.finished_at * 1000).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</time></span>
        <ChevronRight size={13} className="text-muted shrink-0" />
      </button>)}
    </div>
    {!filtered.length && <p className="delegation-history-empty">{pending ? t("common:loading") : onlyIssues ? t("history.noIssues") : t("plan:delegation.panel.noHistory")}</p>}
    {filtered.length > visibleCount && <Button variant="ghost" size="sm" className="w-full mt-2" onClick={() => setVisibleCount(visibleCount + 7)}><ArrowDown size={13} />{t("history.showMore", { count: filtered.length - visibleCount })}</Button>}
    {items.length > 0 && <p className="delegation-history-note">{t("history.window", { count: items.length })}</p>}
  </div>;
}
