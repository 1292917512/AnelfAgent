import { useState } from "react";
import { ChevronDown, FileDiff, Search } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityFileChange } from "@/lib/types";
import { DiffView } from "@/components/common/DiffView";
import { ActivitySourceLabel } from "./ActivityReferences";

/** 本轮文件差异按需展开，沿用活动快照和共享浮层生命周期。 */
export function ActivityChanges({ files, incomplete }: { files: ActivityFileChange[]; incomplete: boolean }) {
  const { t } = useTranslation("workbench");
  const [query, setQuery] = useState("");
  const [opened, setOpened] = useState<string | null>(null);
  const filtered = files.filter((file) => file.path.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return <div className="activity-changes">
    <div className="activity-changes-summary"><FileDiff size={17} className="text-accent" /><strong>{t("activity.changes.files", { count: files.length })}</strong>
      <span className="ml-auto text-ok">+{files.reduce((sum, file) => sum + file.additions, 0)}</span><span className="text-danger">−{files.reduce((sum, file) => sum + file.removals, 0)}</span>
    </div>
    <p className="activity-note">{t("activity.changes.hint")}</p>
    {incomplete && <p role="status" className="activity-note text-warn">{t("activity.changes.incomplete")}</p>}
    {files.length > 0 && <label className="activity-changes-search"><Search size={14} /><input aria-label={t("activity.changes.search")} placeholder={t("activity.changes.search")} value={query} onChange={(event) => setQuery(event.target.value)} /></label>}
    {!filtered.length && <p className="activity-note">{t(query ? "activity.changes.noMatch" : "activity.changes.empty")}</p>}
    {filtered.map((file) => {
      const open = opened === file.path;
      return <section key={file.path} className="activity-change-file">
        <button type="button" aria-expanded={open} className="activity-change-toggle" onClick={() => setOpened(open ? null : file.path)} title={file.path}>
          <ChevronDown size={14} className={open ? "" : "-rotate-90"} /><span className="min-w-0 flex-1 break-all font-mono">{file.path}</span>
          <span className="text-ok">+{file.additions}</span><span className="text-danger">−{file.removals}</span>
        </button>
        {open && <div className="activity-change-edits">{file.edits.map(({ run, entry }, index) => <div key={`${run.id}:${entry.id}`}>
          <div className="activity-change-meta"><span>{t(`activity.changes.${entry.operation ?? (entry.move_from ? "renamed" : "modified")}`)} · {index + 1}/{file.edits.length}</span><ActivitySourceLabel source={run.source} /><span title={run.actor || run.label}>{run.actor || run.label}</span></div>
          <DiffView {...entry} defaultOpen={index === file.edits.length - 1} />
        </div>)}</div>}
      </section>;
    })}
    <p className="activity-note activity-changes-footnote">{t("activity.changes.noUndo")}</p>
  </div>;
}
