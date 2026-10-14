import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Archive, ArchiveRestore, Boxes, ChevronDown, CircleDashed, GitMerge, Pencil, Pin, PinOff, Trash2 } from "lucide-react";
import { skillsApi } from "@/lib/api";
import type { SkillItem } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, Button } from "@/components/ui";
import { ConfirmDialog } from "@/components/ui/Modal";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { Markdown } from "@/pages/chat/render/Markdown";
import { formatRelativeTimestamp } from "@/lib/format";

export function SkillCard({ skill, expanded, onToggle, onEdit, onChanged, onDeleted }: {
  skill: SkillItem; expanded: boolean; onToggle: () => void; onEdit: (skill: SkillItem) => void;
  onChanged: () => void; onDeleted: () => void;
}) {
  const { t } = useTranslation("skills");
  const article = useRef<HTMLElement>(null);
  useEffect(() => { if (expanded) article.current?.scrollIntoView({ block: "nearest", behavior: "instant" }); }, [expanded]);
  const [deleting, setDeleting] = useState(false);
  const query = useQuery({ queryKey: ["skill", skill.name], queryFn: () => skillsApi.get(skill.name).then((r) => r.data), enabled: expanded, throwOnError: false });
  const action = useMutation({
    mutationFn: async (kind: "pin" | "state" | "embed") => {
      if (kind === "pin") await skillsApi.setPinned(skill.name, !skill.pinned);
      else if (kind === "state") await skillsApi.setState(skill.name, skill.state === "archived" ? "active" : "archived");
      else await skillsApi.embed(skill.name);
    }, onSuccess: onChanged,
  });
  const remove = useMutation({ mutationFn: () => skillsApi.remove(skill.name), onSuccess: onDeleted });
  const detail = query.data;
  return <article ref={article} data-skill={skill.name} className={cn("skill-library-card overflow-hidden rounded-xl border bg-card", expanded ? "border-accent/50" : "border-border")}>
    <div className="flex items-center gap-2 p-3 sm:p-4">
      <button type="button" onClick={onToggle} aria-expanded={expanded} className="flex min-w-0 flex-1 items-center gap-3 text-left">
        <ChevronDown size={16} className={cn("shrink-0 text-muted transition-transform", !expanded && "-rotate-90")} />
        <div className="min-w-0 flex-1"><div className="flex items-center gap-2">
          <span className="break-all font-medium">{skill.name}</span>
          {skill.pinned && <Pin size={13} className="shrink-0 text-warn" />}
          {skill.merged_into && <GitMerge size={13} className="shrink-0 text-muted" aria-label={t("mergedInto", { name: skill.merged_into })} />}
        </div><p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{skill.description}</p></div>
        <Badge variant={skill.state === "active" ? "ok" : skill.state === "stale" ? "warn" : "neutral"}>
          {skill.state === "active" ? t("stateActive") : skill.state === "stale" ? t("stateStale") : t("stateArchived")}
        </Badge>
      </button>
      {skill.embedded != null && <Button size="icon" variant="ghost" title={skill.embedded ? t("embeddingRegenerate") : t("embeddingGenerate")}
        onClick={() => action.mutate("embed")} loading={action.isPending}>
        {skill.embedded ? <Boxes size={15} className="text-ok" /> : <CircleDashed size={15} />}
      </Button>}
    </div>
    <div className="skill-card-facts"><span>{t("matchCount")} <b>{skill.match_count}</b></span><span>{t("useCount")} <b>{skill.use_count}</b></span><span>{t("patchCount")} <b>{skill.patch_count}</b></span>
      {skill.last_activity_at > 0 && <span className="ml-auto">{t("lastActivity")} {formatRelativeTimestamp(skill.last_activity_at)}</span>}
    </div>
    {!expanded && !!skill.trigger_patterns.length && <div className="skill-card-triggers">{skill.trigger_patterns.slice(0, 4).map((pattern) => <span key={pattern}>{pattern}</span>)}{skill.trigger_patterns.length > 4 && <span>+{skill.trigger_patterns.length - 4}</span>}</div>}
    {action.error && <div className="px-4 pb-3"><QueryError compact error={action.error} /></div>}
    {expanded && <div className="space-y-4 border-t border-border p-4">
      <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
        {detail && <>
          <div className="flex flex-wrap gap-3 text-xs text-muted">
            <span>{t("createdBy")}: {detail.created_by === "agent" ? t("createdByAgent") : t("createdByUser")}</span>
            {detail.merged_into && <span>{t("mergedInto", { name: detail.merged_into })}</span>}
          </div>
          {!!detail.trigger_patterns.length && <div className="flex flex-wrap gap-2">{detail.trigger_patterns.map((pattern) => <Badge key={pattern}>{pattern}</Badge>)}</div>}
          {detail.rationale && <p className="rounded-lg bg-elevated p-3 text-xs text-muted">{t("rationale")}: {detail.rationale}</p>}
          <div className="skill-content-preview"><Markdown content={detail.content ?? ""} fileBase={{ root: "workspace", path: `skills/${skill.name}` }} /></div>
          <details className="skill-source"><summary>{t("library.markdownSource")}</summary><pre>{detail.content}</pre></details>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => onEdit(detail)}><Pencil size={14} />{t("edit")}</Button>
            <Button size="sm" disabled={action.isPending} onClick={() => action.mutate("pin")}>{skill.pinned ? <PinOff size={14} /> : <Pin size={14} />}{skill.pinned ? t("unpin") : t("pin")}</Button>
            <Button size="sm" disabled={action.isPending} onClick={() => action.mutate("state")}>{skill.state === "archived" ? <ArchiveRestore size={14} /> : <Archive size={14} />}{skill.state === "archived" ? t("unarchive") : t("archive")}</Button>
            <Button size="sm" variant="danger" onClick={() => setDeleting(true)}><Trash2 size={14} />{t("delete")}</Button>
          </div>
        </>}
      </AsyncState>
    </div>}
    <ConfirmDialog open={deleting} onClose={() => setDeleting(false)} title={t("delete")} danger loading={remove.isPending}
      message={<>{t("deleteConfirm")}{remove.error && <div className="mt-3"><QueryError compact error={remove.error} /></div>}</>}
      onConfirm={() => remove.mutate()} />
  </article>;
}
