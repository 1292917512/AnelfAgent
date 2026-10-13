import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

function withinFeed(event: React.SyntheticEvent<HTMLDivElement>): boolean {
  return event.target instanceof Node && event.currentTarget.contains(event.target);
}

/** 跟随时间流末尾及异步布局；只有用户滚动或显式暂停才停止跟随。 */
export function useActivityFollow(revision: string, onPause: () => void) {
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const enabled = useRef(true);
  const [following, setFollowing] = useState(true);
  const touchY = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const pause = useCallback(() => {
    enabled.current = false;
    setFollowing(false);
    onPause();
  }, [onPause]);
  const align = useCallback(() => {
    const container = scroll.current;
    if (!enabled.current || !container) return;
    container.scrollTop = container.scrollHeight;
  }, []);
  const schedule = useCallback(() => {
    if (frame.current != null) return;
    frame.current = requestAnimationFrame(() => { frame.current = null; align(); });
  }, [align]);
  const resume = useCallback(() => {
    enabled.current = true;
    setFollowing(true);
    schedule();
  }, [schedule]);
  useLayoutEffect(() => { schedule(); }, [revision, schedule]);
  useEffect(() => {
    const observer = new ResizeObserver(schedule);
    if (content.current) observer.observe(content.current);
    if (scroll.current) observer.observe(scroll.current);
    return () => {
      observer.disconnect();
      if (frame.current != null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, [schedule]);
  return { scroll, content, following, pause, resume, handlers: {
    onWheel: (event: React.WheelEvent<HTMLDivElement>) => { if (withinFeed(event) && event.deltaY !== 0) pause(); },
    onTouchStart: (event: React.TouchEvent<HTMLDivElement>) => { touchY.current = withinFeed(event) ? event.touches[0]?.clientY ?? null : null; },
    onTouchMove: (event: React.TouchEvent<HTMLDivElement>) => {
      if (touchY.current != null && Math.abs((event.touches[0]?.clientY ?? touchY.current) - touchY.current) > 6) pause();
    },
    onTouchEnd: () => { touchY.current = null; },
    onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => {
      if (!withinFeed(event)) return;
      const element = event.currentTarget;
      const rect = element.getBoundingClientRect();
      if (event.clientX >= rect.right - (element.offsetWidth - element.clientWidth)) pause();
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (!withinFeed(event)) return;
      if (event.target instanceof Element && event.target.closest("input, textarea, button, a, summary, [contenteditable=true]")) return;
      if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) pause();
    },
  } };
}
