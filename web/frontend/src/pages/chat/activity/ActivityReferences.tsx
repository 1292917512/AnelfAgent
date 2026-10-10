import { Fragment } from "react";
import { Hash, Radio, UserRound, Users, ArrowUpRight } from "lucide-react";
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
  if (["path", "file_path", "directory", "media_file", "file", "dir"].includes(tag.key)) {
    const ref = parseFileReference(`./${root === "project" ? "project:" : ""}${["directory", "dir"].includes(tag.key) ? "dir:" : ""}${encodeURIComponent(tag.value)}`);
    if (ref) return <FileReferenceLink reference={ref}>{tag.value}</FileReferenceLink>;
  }
  return <span className="activity-tag" title={`${tag.key}: ${tag.value}`}><span>{t(`activity.tags.${tag.key}`, { defaultValue: tag.key })}</span><b>{tag.value}</b></span>;
}

export function ActivityTargets({ targets }: { targets: ActivityTag[] }) {
  const { t } = useTranslation("workbench");
  if (!targets.length) return null;
  const root = targets.find((tag) => tag.key === "root")?.value;
  return <div className="activity-targets" aria-label={t("activity.target")}><ArrowUpRight size={13} className="shrink-0 text-muted" /><span className="sr-only">{t("activity.target")}</span>{targets.filter((tag) => tag.key !== "root").map((tag, index) => <ActivityTagLabel key={`${tag.key}:${index}`} tag={tag} root={root} />)}</div>;
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
