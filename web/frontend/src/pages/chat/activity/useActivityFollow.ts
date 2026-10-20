import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

const BOTTOM_GAP = 32;

/** 内层滚动仍有余量时，手势属于内层内容。 */
function consumesScroll(target: EventTarget | null, feed: HTMLElement, delta: number): boolean {
  let element = target instanceof Element ? target : null;
  while (element && element !== feed) {
    if (element instanceof HTMLElement && /auto|scroll/.test(getComputedStyle(element).overflowY)
      && (delta < 0 ? element.scrollTop > 0 : element.scrollTop + element.clientHeight < element.scrollHeight - 1)) return true;
    element = element.parentElement;
  }
  return false;
}

/** 跟随布局末尾；上翻阅读暂停，滚回底部恢复，显式暂停保持到手动恢复。 */
export function useActivityFollow(revision: string, onPause: () => void, current = true) {
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const enabled = useRef(true);
  const manual = useRef(false);
  const [following, setFollowing] = useState(true);
  const touchY = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const lastScrollTop = useRef(0);
  const cancelFrame = useCallback(() => {
    if (frame.current != null) cancelAnimationFrame(frame.current);
    frame.current = null;
  }, []);
  const pauseReading = useCallback(() => {
    cancelFrame();
    if (!enabled.current) return;
    enabled.current = false;
    lastScrollTop.current = scroll.current?.scrollTop ?? 0;
    setFollowing(false);
    onPause();
  }, [onPause, cancelFrame]);
  const pause = useCallback(() => { manual.current = true; pauseReading(); }, [pauseReading]);
  const schedule = useCallback(() => {
    if (!enabled.current || frame.current != null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      const container = scroll.current;
      if (enabled.current && container) {
        const bottom = Math.max(0, container.scrollHeight - container.clientHeight);
        if (Math.abs(container.scrollTop - bottom) > 1) container.scrollTop = bottom;
        lastScrollTop.current = container.scrollTop;
      }
    });
  }, []);
  const resume = useCallback(() => {
    manual.current = false;
    enabled.current = true;
    setFollowing(true);
    schedule();
  }, [schedule]);
  useLayoutEffect(schedule, [revision, schedule]);
  useEffect(() => {
    const observer = new ResizeObserver(schedule);
    if (content.current) observer.observe(content.current);
    if (scroll.current) observer.observe(scroll.current);
    const visible = () => { if (!document.hidden) schedule(); };
    document.addEventListener("visibilitychange", visible);
    return () => { observer.disconnect(); cancelFrame(); document.removeEventListener("visibilitychange", visible); };
  }, [schedule, cancelFrame]);
  return { scroll, content, following, pause, resume, handlers: {
    onScroll: (event: React.UIEvent<HTMLDivElement>) => {
      if (event.target !== event.currentTarget) return;
      const element = event.currentTarget;
      const downward = element.scrollTop > lastScrollTop.current;
      lastScrollTop.current = element.scrollTop;
      if (!enabled.current && !manual.current && current && downward
        && element.scrollHeight - element.clientHeight - element.scrollTop <= BOTTOM_GAP) resume();
    },
    onWheel: (event: React.WheelEvent<HTMLDivElement>) => {
      if (event.target instanceof Node && event.currentTarget.contains(event.target) && event.deltaY < 0
        && Math.abs(event.deltaY) >= Math.abs(event.deltaX) && !consumesScroll(event.target, event.currentTarget, event.deltaY)) pauseReading();
    },
    onTouchStart: (event: React.TouchEvent<HTMLDivElement>) => { touchY.current = event.touches[0]?.clientY ?? null; },
    onTouchMove: (event: React.TouchEvent<HTMLDivElement>) => {
      const y = event.touches[0]?.clientY;
      if (y == null || touchY.current == null) return;
      const delta = touchY.current - y;
      if (Math.abs(delta) > 6) {
        if (delta < 0 && !consumesScroll(event.target, event.currentTarget, delta)) pauseReading();
        touchY.current = y;
      }
    },
    onTouchEnd: () => { touchY.current = null; },
    onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => {
      const element = event.currentTarget;
      if (event.target === element && event.clientX >= element.getBoundingClientRect().right - (element.offsetWidth - element.clientWidth)) pauseReading();
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (event.target instanceof Element && event.target.closest("input, textarea, button, a, summary, [contenteditable=true]")) return;
      if (["ArrowUp", "PageUp", "Home"].includes(event.key) && !consumesScroll(event.target, event.currentTarget, -1)) pauseReading();
    },
  } };
}
