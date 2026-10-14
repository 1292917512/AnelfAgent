import { Bell, Send, MessagesSquare, Radio, ArrowUpRight, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ActivityEntry } from "@/lib/types/activity";
import { parseMessageTags } from "@/lib/message-tags";
import { ActivityInspector } from "./ActivityInspector";
import { ActivitySourceLabel, renderTaggedText } from "./ActivityReferences";
import { Markdown } from "../render/Markdown";

/** 已进入当前执行上下文的消息与通知，详情保留频道、身份和原文标签。 */
export function ActivityNotice({ entry }: { entry: Extract<ActivityEntry, { kind: "notice" }> }) {
  const { t } = useTranslation("workbench");
  const title = t(`activity.updates.${entry.origin}`);
  const Icon = entry.origin === "recovery" ? Wrench : entry.origin === "delivery" ? Send : entry.origin === "push" ? Bell : entry.origin === "cross_channel" ? Radio : MessagesSquare;
  const content = entry.items[0]?.content ?? "";
  const tags = parseMessageTags(content);
  const segments = tags.map((tag, index) => content.slice(tags[index - 1]?.end ?? 0, tag.start));
  const preview = [...segments, content.slice(tags[tags.length - 1]?.end ?? 0)].join("").trim().slice(0, 300);
  const sources = Array.from(new Set(entry.items.map((item) => item.source.channel).filter(Boolean)));
  return <div className="activity-notice">
    <ActivityInspector title={title} preview={preview} className="activity-notice-trigger" trigger={<>
      <span className="activity-notice-symbol"><Icon size={15} /></span>
      <span className="activity-notice-body"><span className="activity-notice-title">{title}<span>{t("activity.updateCount", { count: entry.count })}</span></span>
        <span className="activity-notice-preview">{preview || t("activity.updateTagged")}</span>
        <span className="activity-notice-meta">{t("activity.updateRound", { count: entry.iteration })}{sources.length > 0 && ` · ${sources.join(" / ")}`}</span>
      </span><ArrowUpRight size={15} className="shrink-0 text-muted" />
    </>}>
      <p className="activity-note">{t(entry.origin === "recovery" ? "activity.recoveryHint" : entry.origin === "delivery" ? "activity.deliveryHint" : "activity.updateHint", { count: entry.iteration })}</p>
      {entry.truncated && <p className="activity-note">{t("activity.truncated")}</p>}
      {entry.deferred > 0 && <p className="activity-note">{t("activity.updateDeferred", { count: entry.deferred })}</p>}
      <div className="activity-notice-items">{entry.items.map((item, index) => <article key={`${item.source.scope}:${item.message_id}:${index}`}>
        <header><ActivitySourceLabel source={item.source} /><span>{t(`activity.updateRoles.${item.role}`, { defaultValue: item.role })}</span>
          {item.ts > 0 && <time dateTime={new Date(item.ts * 1000).toISOString()}>{new Date(item.ts * 1000).toLocaleTimeString()}</time>}
        </header>
        <Markdown content={item.content} renderText={renderTaggedText} />
        {item.message_id && <p className="activity-trace-id">{t("activity.tags.message_id")}: {item.message_id}</p>}
      </article>)}</div>
    </ActivityInspector>
  </div>;
}
