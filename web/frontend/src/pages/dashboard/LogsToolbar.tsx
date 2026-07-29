import { useTranslation } from "react-i18next";
import { ArrowDownToLine, OctagonAlert, Pause, Play, Search, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";

const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] as const;

const LEVEL_CHIP: Record<string, string> = {
  DEBUG: "border-border text-muted",
  INFO: "border-[rgba(59,130,246,0.4)] text-info",
  WARNING: "border-[rgba(245,158,11,0.4)] text-warn",
  ERROR: "border-[rgba(239,68,68,0.4)] text-danger",
  CRITICAL: "border-[rgba(239,68,68,0.4)] text-danger",
};

/** 日志过滤工具栏：级别多选 / 只看错误 / 标签 / 关键词 / 暂停跟随 / 清空 */
export function LogsToolbar({
  levels,
  onToggleLevel,
  byLevel,
  onlyErrors,
  onToggleOnlyErrors,
  tag,
  onTagChange,
  tagOptions,
  byTag,
  keyword,
  onKeywordChange,
  paused,
  onTogglePause,
  pendingCount,
  following,
  onFollow,
  onClear,
}: {
  levels: Set<string>;
  onToggleLevel: (level: string) => void;
  byLevel: Record<string, number>;
  onlyErrors: boolean;
  onToggleOnlyErrors: () => void;
  tag: string;
  onTagChange: (tag: string) => void;
  tagOptions: string[];
  byTag: Record<string, number>;
  keyword: string;
  onKeywordChange: (kw: string) => void;
  paused: boolean;
  onTogglePause: () => void;
  pendingCount: number;
  following: boolean;
  onFollow: () => void;
  onClear: () => void;
}) {
  const { t } = useTranslation("status");
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        {LEVELS.map((lv) => {
          const active = levels.has(lv);
          return (
            <button
              key={lv}
              onClick={() => onToggleLevel(lv)}
              className={cn(
                "px-2 py-1 text-[11px] font-medium rounded-full border transition-all",
                active ? cn(LEVEL_CHIP[lv], "bg-card") : "border-border text-muted opacity-40",
              )}
            >
              {t(`levelLabels.${lv.toLowerCase()}`)}
              {byLevel[lv] != null && <span className="ml-1 opacity-70">{byLevel[lv]}</span>}
            </button>
          );
        })}
        <button
          onClick={onToggleOnlyErrors}
          className={cn(
            "flex items-center gap-1 px-2 py-1 text-[11px] font-medium rounded-full border transition-all",
            onlyErrors
              ? "border-[rgba(239,68,68,0.5)] text-danger bg-danger-subtle"
              : "border-border text-muted hover:text-foreground",
          )}
        >
          <OctagonAlert size={11} /> {t("logsView.onlyErrors")}
        </button>
      </div>

      <select
        value={tag}
        onChange={(e) => onTagChange(e.target.value)}
        className="bg-elevated border border-input rounded-md px-2 py-1 text-xs text-foreground outline-none"
      >
        <option value="">{t("allTags")}</option>
        {tagOptions.map((tagOpt) => (
          <option key={tagOpt} value={tagOpt}>
            {tagOpt} ({byTag[tagOpt]})
          </option>
        ))}
      </select>

      <div className="relative flex-1 min-w-[140px] max-w-xs">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
        <input
          value={keyword}
          onChange={(e) => onKeywordChange(e.target.value)}
          placeholder={t("searchKeyword")}
          className="w-full pl-8 pr-3 py-1.5 text-xs bg-elevated border border-input rounded-md text-foreground outline-none focus:border-ring"
        />
      </div>

      <div className="flex items-center gap-1.5 ml-auto">
        <button
          onClick={onTogglePause}
          title={paused ? t("logsView.resume") : t("logsView.pause")}
          className={cn(
            "flex items-center gap-1 px-2.5 py-1.5 text-[11px] font-medium rounded-md border transition-all",
            paused
              ? "bg-warn-subtle text-warn border-[var(--warn)]"
              : "bg-secondary text-muted border-border hover:text-foreground",
          )}
        >
          {paused ? <Play size={12} /> : <Pause size={12} />}
          {paused ? `${t("logsView.resume")}${pendingCount > 0 ? ` (+${pendingCount})` : ""}` : t("logsView.pause")}
        </button>
        <button
          onClick={onFollow}
          title={t("logsView.follow")}
          aria-label={t("logsView.follow")}
          className={cn(
            "p-1.5 rounded-md border transition-all",
            following
              ? "bg-accent-subtle text-accent border-accent"
              : "bg-secondary text-muted border-border hover:text-foreground",
          )}
        >
          <ArrowDownToLine size={14} />
        </button>
        <button
          onClick={onClear}
          title={t("logsView.clear")}
          aria-label={t("logsView.clear")}
          className="p-1.5 rounded-md border bg-secondary text-muted border-border hover:text-danger transition-all"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}
