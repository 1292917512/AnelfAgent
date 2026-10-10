import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Activity, ArrowDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useActivityStore } from "@/stores/activity-store";
import { useChatStore } from "@/stores/chat-store";
import { QueryError } from "@/components/common/AsyncState";
import { Button } from "@/components/ui";
import { ActivityRunCard } from "./activity/ActivityRunCard";

/** 各会话共享的实时过程窗口，持续展示中间输出，不依赖详细追踪。 */
export default function ExecutionWorkspace() {
  const { t } = useTranslation("workbench");
  const { runs, loaded, error, load } = useActivityStore();
  const connected = useChatStore((state) => state.sseConnected);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [behind, setBehind] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const toggleRun = useCallback((id: string, open: boolean) => {
    setExpanded((current) => ({ ...current, [id]: open }));
  }, []);
  const revealRun = useCallback((id: string) => {
    toggleRun(id, true); follow.current = false;
    requestAnimationFrame(() => {
      const container = scroll.current;
      const target = document.getElementById(`activity-${id}`);
      if (container && target) container.scrollTo({ top: container.scrollTop + target.getBoundingClientRect().top - container.getBoundingClientRect().top - 18, behavior: "instant" });
    });
  }, [toggleRun]);
  const running = runs.filter((run) => run.status === "running").length;
  useEffect(() => { if (!loaded) void load(); }, [loaded, load]);
  useLayoutEffect(() => {
    if (follow.current && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [runs]);
  return <section aria-label={t("execution.region")} className="activity-pane flex h-full min-h-0 flex-col">
    <header className="activity-heading"><Activity size={16} className="text-accent" /><h2>{t("activity.title")}</h2>
      <span className="ml-auto inline-flex items-center gap-2 text-xs text-muted" role="status">{running > 0 && <span className="activity-live-dot" />}{t(!connected ? "activity.reconnecting" : running ? "activity.working" : "activity.idle")}</span>
    </header>
    {error != null && <div className="p-3"><QueryError compact error={error} retry={() => void load()} /></div>}
    <div className="relative min-h-0 flex-1">
      <div ref={scroll} className="activity-feed h-full overflow-y-auto overscroll-contain" onScroll={() => {
        const element = scroll.current;
        if (!element) return;
        follow.current = element.scrollHeight - element.scrollTop - element.clientHeight < 100;
        setBehind(!follow.current);
      }}>
        {!runs.length && <div className="activity-empty"><span className="welcome-orbit" aria-hidden="true"><span /></span><h3>{t("activity.empty")}</h3><p>{t("activity.emptyHint")}</p></div>}
        {runs.map((run, index) => <ActivityRunCard key={run.id} run={run} open={expanded[run.id] ?? (run.status === "running" || index === runs.length - 1)} onToggle={toggleRun} onReveal={revealRun} />)}
      </div>
      {behind && <Button className="activity-follow" size="sm" onClick={() => { follow.current = true; setBehind(false); scroll.current?.scrollTo({ top: scroll.current.scrollHeight, behavior: "instant" }); }}><ArrowDown size={14} />{t("activity.latest")}</Button>}
    </div>
  </section>;
}
