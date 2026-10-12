import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { activityRecord } from "@/lib/activity-data";
import { TaggedText } from "./ActivityReferences";

/** 在折叠工具行直接呈现检索意图及操作对象，完整回执仍在详情中。 */
export function ActivityToolSummary({ entry }: { entry: Extract<ActivityEntry, { kind: "tool" }> }) {
  const { t } = useTranslation("skills");
  const args = activityRecord(entry.arguments);
  const result = activityRecord(entry.result);
  let label = "";
  let text = "";
  let outcome = "";
  if (["get_skill", "restore_skill", "update_skill", "create_skill"].includes(entry.name) && typeof args.name === "string") {
    label = t("node.skill"); text = args.name;
    if (entry.status === "done" && entry.name === "get_skill") outcome = t("evidence.read");
  } else if (["recall", "recall_tool_errors"].includes(entry.name)) {
    label = t("node.query"); text = typeof args.query === "string" ? args.query : typeof args.tool_name === "string" ? args.tool_name : "";
    if (typeof args.tags === "string" && args.tags) text += ` · ${t("node.tags")}: ${args.tags}`;
    if (typeof args.filter_tags === "string" && args.filter_tags) text += ` · ${t("node.filterTags")}: ${args.filter_tags}`;
    const count = typeof result.count === "number" ? result.count : Array.isArray(result.results) ? result.results.length : null;
    if (entry.status === "done" && count != null) outcome = t("node.results", { count });
  } else if (typeof args.group === "string") {
    label = t("node.group"); text = typeof args.group === "string" ? args.group : "";
  }
  if (!text.trim() && !outcome) return null;
  return <div className="activity-tool-summary"><span>{label}</span><p><TaggedText content={text.trim().slice(0, 240)} /></p>{outcome && <b>{outcome}</b>}</div>;
}
