import { useTranslation } from "react-i18next";
import { ArrowDownToLine, OctagonAlert, Pause, Play, Search, Trash2, X } from "lucide-react";
import { Button, Input, Select } from "@/components/ui";
import { LOG_LEVELS } from "./log-buffer";

export function LogsToolbar({
  levels, onToggleLevel, byLevel, onlyErrors, onToggleOnlyErrors,
  tag, onTagChange, tagOptions, byTag, keyword, onKeywordChange,
  paused, onTogglePause, pendingCount, following, onFollow, onClear,
}: {
  levels: Set<string>; onToggleLevel: (level: string) => void; byLevel: Record<string, number>;
  onlyErrors: boolean; onToggleOnlyErrors: () => void;
  tag: string; onTagChange: (tag: string) => void; tagOptions: string[]; byTag: Record<string, number>;
  keyword: string; onKeywordChange: (keyword: string) => void;
  paused: boolean; onTogglePause: () => void; pendingCount: number;
  following: boolean; onFollow: () => void; onClear: () => void;
}) {
  const { t } = useTranslation("status");
  return <div className="logs-toolbar">
    <div className="logs-search-row">
      <div className="logs-search"><Search size={15} /><Input aria-label={t("searchKeyword")} placeholder={t("searchKeyword")} value={keyword} onChange={(event) => onKeywordChange(event.target.value)} />
        {keyword && <button type="button" aria-label={t("logsView.clearSearch")} onClick={() => onKeywordChange("")}><X size={14} /></button>}
      </div>
      <Select aria-label={t("allTags")} value={tag} onChange={(event) => onTagChange(event.target.value)} className="logs-tag-select">
        <option value="">{t("allTags")}</option>
        {[...new Set([...tagOptions, ...(tag ? [tag] : [])])].map((value) => <option key={value} value={value}>{value} · {byTag[value] ?? 0}</option>)}
      </Select>
      <div className="logs-toolbar-actions">
        <Button variant="ghost" size="sm" onClick={onTogglePause} aria-pressed={paused}>{paused ? <Play size={14} /> : <Pause size={14} />}{paused ? t("logsView.resume") : t("logsView.pause")}{paused && pendingCount > 0 && <span>+{pendingCount}</span>}</Button>
        <Button variant="ghost" size="icon" onClick={onFollow} aria-pressed={following && !paused} title={t("logsView.follow")}><ArrowDownToLine size={16} /></Button>
        <Button variant="ghost" size="icon" onClick={onClear} title={t("logsView.clear")}><Trash2 size={15} /></Button>
      </div>
    </div>
    <div className="logs-level-row">
      <div className="logs-levels" role="group" aria-label={t("logsView.levels")}>
        {LOG_LEVELS.map((level) => <button type="button" key={level} onClick={() => onToggleLevel(level)} aria-pressed={levels.has(level)} data-level={level}>
          <i aria-hidden="true" />{t(`levelLabels.${level.toLowerCase()}`)}<span>{byLevel[level] ?? 0}</span>
        </button>)}
      </div>
      <button type="button" className="logs-errors-filter" aria-label={t("logsView.onlyErrors")} title={t("logsView.onlyErrors")} aria-pressed={onlyErrors} onClick={onToggleOnlyErrors}><OctagonAlert size={14} /><span>{t("logsView.onlyErrors")}</span></button>
    </div>
  </div>;
}
