import { useEffect, useMemo, useState, type RefObject } from "react";
import * as Popover from "@radix-ui/react-popover";
import { useTranslation } from "react-i18next";
import type { ActivitySegment } from "./activity-stream";
import { activityStages, type ActivityStage } from "./activity-stages";

function StageMarker({ stage, active, navigate }: { stage: ActivityStage; active: boolean; navigate: () => void }) {
  const { t } = useTranslation("workbench");
  const [open, setOpen] = useState(false);
  const label = `${t(`activity.stages.${stage.kind}`)} · ${stage.preview || stage.title}`;
  return <Popover.Root open={open} onOpenChange={setOpen}>
    <Popover.Anchor asChild><button type="button" className="activity-stage-marker" aria-label={label}
      aria-current={active ? "step" : undefined} data-running={stage.running} data-failed={stage.failed}
      onPointerEnter={(event) => { if (event.pointerType === "mouse") setOpen(true); }}
      onPointerLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onClick={() => { setOpen(false); navigate(); }}><span /></button></Popover.Anchor>
    <Popover.Portal><Popover.Content side="right" sideOffset={6} collisionPadding={12}
      className="activity-stage-preview" aria-label={label} onOpenAutoFocus={(event) => event.preventDefault()} onCloseAutoFocus={(event) => event.preventDefault()}
      onPointerEnter={() => setOpen(true)} onPointerLeave={() => setOpen(false)}>
      <strong>{t(`activity.stages.${stage.kind}`)}{stage.count > 1 && ` · ${stage.count}`}</strong>
      <p>{stage.title}</p><small>{stage.preview}</small>
    </Popover.Content></Popover.Portal>
  </Popover.Root>;
}

/** 阶段定位暂停自动跟随；悬停只读轻量预览，不挂载工具或 Markdown 详情。 */
export function ActivityStageRail({ segments, scroll, onNavigate }: { segments: ActivitySegment[]; scroll: RefObject<HTMLDivElement>; onNavigate: () => void }) {
  const { t } = useTranslation("workbench");
  const stages = useMemo(() => activityStages(segments), [segments]);
  const [active, setActive] = useState("");
  const signature = JSON.stringify(stages.map(({ id, runId, entryIds }) => ({ id, runId, entryIds })));
  useEffect(() => {
    const root = scroll.current;
    if (!root) return;
    const anchors: Pick<ActivityStage, "id" | "runId" | "entryIds">[] = JSON.parse(signature);
    const owners = new Map(anchors.flatMap((stage) => stage.entryIds.map((id) => [`${stage.runId}:${id}`, stage.id] as const)));
    const visible = new Map<Element, string>();
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const item = entry.target;
        if (!(item instanceof HTMLElement)) continue;
        const id = owners.get(`${item.closest<HTMLElement>("[data-activity-run]")?.dataset.activityRun}:${item.dataset.activityEntry}`);
        if (entry.isIntersecting && id) visible.set(item, id);
        else visible.delete(item);
      }
      const first = [...visible].sort(([a], [b]) => a.getBoundingClientRect().top - b.getBoundingClientRect().top)[0];
      if (first) setActive(first[1]);
    }, { root, rootMargin: "0px 0px -45% 0px", threshold: 0 });
    root.querySelectorAll("[data-activity-entry]").forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, [scroll, signature]);
  if (!stages.length) return null;
  return <nav className="activity-stage-rail" aria-label={t("activity.stages.title")}>
    {stages.map((stage) => <StageMarker key={stage.id} stage={stage} active={active === stage.id} navigate={() => {
      const root = scroll.current;
      const element = root?.querySelector<HTMLElement>(`[data-activity-run="${CSS.escape(stage.runId)}"] [data-activity-entry="${CSS.escape(stage.entryId)}"]`);
      if (!root || !element) return;
      onNavigate(); setActive(stage.id);
      root.scrollTo({ top: root.scrollTop + element.getBoundingClientRect().top - root.getBoundingClientRect().top - 12, behavior: "auto" });
    }} />)}
  </nav>;
}
