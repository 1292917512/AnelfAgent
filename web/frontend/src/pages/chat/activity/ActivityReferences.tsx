import { Fragment } from "react";
import { Hash, Radio, UserRound, Users, ArrowUpRight, GraduationCap } from "lucide-react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ActivitySource, ActivityTag } from "@/lib/types/activity";
import { parseMessageTags } from "@/lib/message-tags";
import { parseFileReference } from "@/lib/file-reference";
import { FileReferenceLink } from "../render/FileReferenceLink";

export function ActivitySourceLabel({ source }: { source: ActivitySource }) {
  const { t } = useTranslation("workbench");
  return <span className="activity-source" title={source.scope}>
    {source.channel && <span><Radio size={12} />{source.channel}</span>}
    {source.target && <span>{source.kind === "group" ? <Users size={12} /> : <UserRound size={12} />}{t(`activity.${source.kind === "group" ? "group" : "user"}`)} {source.target}</span>}
    {source.session && <span><Hash size={11} />{source.session}</span>}
    {!source.channel && !source.target && <span>{t("activity.system")}</span>}
  </span>;
}

export function ActivityTagLabel({ tag, root = "workspace" }: { tag: ActivityTag; root?: string }) {
  const { t } = useTranslation("workbench");
  const fileRoot = tag.root ?? root;
  if (tag.key === "skill") return <Link className="activity-tag text-accent" to={`/skills?skill=${encodeURIComponent(tag.value)}`}><GraduationCap size={12} /><b>{tag.value}</b></Link>;
  if (["workspace", "project"].includes(fileRoot) && ["path", "file_path", "directory", "media_file", "file", "dir"].includes(tag.key)) {
    const ref = parseFileReference(`./${fileRoot === "project" ? "project:" : ""}${["directory", "dir"].includes(tag.key) ? "dir:" : ""}${encodeURIComponent(tag.path ?? tag.value)}`);
    if (ref) return <FileReferenceLink reference={ref}>{tag.value}</FileReferenceLink>;
  }
  const value = tag.key === "delivery_mode" ? t(`activity.deliveryModes.${tag.value}`, { defaultValue: tag.value })
    : tag.key === "delivery_status" ? t(`activity.deliveryStates.${tag.value}`, { defaultValue: tag.value }) : tag.value;
  return <span className="activity-tag" title={`${tag.key}: ${tag.path ?? tag.value}`}><span>{t(`activity.tags.${tag.key}`, { defaultValue: tag.key })}</span><b>{value}</b></span>;
}

export function ActivityTargets({ targets, compact = false }: { targets: ActivityTag[]; compact?: boolean }) {
  const { t } = useTranslation("workbench");
  if (!targets.length) return null;
  const root = targets.find((tag) => tag.key === "root")?.value;
  const visible = targets.filter((tag) => tag.key !== "root");
  return <div className={`activity-targets${compact ? " is-compact" : ""}`} aria-label={t("activity.target")}><ArrowUpRight size={13} className="shrink-0 text-muted" /><span className="sr-only">{t("activity.target")}</span>{(compact ? visible.slice(0, 3) : visible).map((tag, index) => <ActivityTagLabel key={`${tag.key}:${index}`} tag={tag} root={root} />)}{compact && visible.length > 3 && <span className="activity-note">+{visible.length - 3}</span>}</div>;
}

/** 记忆标签保留键和值，和正文标签使用相同的语义样式。 */
export function MemoryTags({ tags }: { tags: string[] }) {
  return <span className="activity-memory-tags">{tags.map((value, index) => {
    const separator = value.indexOf(":");
    return separator > 0 ? <ActivityTagLabel key={index} tag={{ key: value.slice(0, separator), value: value.slice(separator + 1), root: "external" }} />
      : <span className="activity-tag" key={index}><b>{value}</b></span>;
  })}</span>;
}

export function TaggedText({ content }: { content: string }) {
  const tags = parseMessageTags(content);
  return <>{tags.map((tag, index) => {
    const text = content.slice(tags[index - 1]?.end ?? 0, tag.start);
    return <Fragment key={tag.start}>{text}<ActivityTagLabel tag={tag} /></Fragment>;
  })}{content.slice(tags[tags.length - 1]?.end ?? 0)}</>;
}

export function renderTaggedText(content: string) {
  return <TaggedText content={content} />;
}
