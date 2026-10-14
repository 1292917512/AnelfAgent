import { useTranslation } from "react-i18next";
import { BookOpen, Search, Wrench } from "lucide-react";
import type { ActivityEntry } from "@/lib/types/activity";
import { activityRecord } from "@/lib/activity-data";
import { MemoryTags, TaggedText } from "./ActivityReferences";

/** 在折叠工具行直接呈现检索意图及操作对象，完整回执仍在详情中。 */
export function ActivityToolSummary({ entry }: { entry: Extract<ActivityEntry, { kind: "tool" }> }) {
  const { t } = useTranslation("skills");
  const args = activityRecord(entry.arguments);
  const result = activityRecord(entry.result);
  let label = "";
  let text = "";
  let outcome = "";
  let Icon = Wrench;
  const tagGroups = ["tags", "filter_tags"].flatMap((key) => {
    const value = args[key];
    return typeof value === "string" && value.trim() ? [{ key, tags: [...new Set(value.split(",").map((tag) => tag.trim()).filter(Boolean))] }] : [];
  });
  if (["get_skill", "restore_skill", "update_skill", "create_skill"].includes(entry.name) && typeof args.name === "string") {
    Icon = BookOpen;
    label = t("node.skill"); text = args.name;
    if (entry.status === "done" && entry.name === "get_skill") outcome = t("evidence.read");
  } else if (["recall", "recall_tool_errors"].includes(entry.name)) {
    Icon = Search;
    label = t("node.query"); text = typeof args.query === "string" ? args.query : typeof args.tool_name === "string" ? args.tool_name : "";
    const count = typeof result.count === "number" ? result.count : Array.isArray(result.results) ? result.results.length : null;
    if (entry.status === "done" && count != null) outcome = t("node.results", { count });
  } else if (typeof args.group === "string") {
    label = t("node.group"); text = typeof args.group === "string" ? args.group : "";
  }
  const detail = [result.error, result.hint, result.description, result.message, result.content, result.output]
    .find((value): value is string => typeof value === "string" && value.trim().length > 0)
    ?? (!Object.keys(result).length ? entry.result : "");
  if (!text.trim() && !outcome && !detail) return null;
  return <div className="activity-tool-summary">
    {(text || outcome) && <div className="activity-summary-line"><Icon size={13} /><span>{label}</span><p><TaggedText content={text.trim().slice(0, 180)} /></p>{outcome && <b>{outcome}</b>}</div>}
    {detail && <p className={entry.status === "error" ? "text-warn" : undefined}><TaggedText content={detail.trim().slice(0, 180)} /></p>}
    {tagGroups.map((group) => <div className="activity-summary-line" key={group.key}><span>{t(group.key === "tags" ? "node.tags" : "node.filterTags")}</span><MemoryTags tags={group.tags.slice(0, 3)} />{group.tags.length > 3 && <span>+{group.tags.length - 3}</span>}</div>)}
  </div>;
}
