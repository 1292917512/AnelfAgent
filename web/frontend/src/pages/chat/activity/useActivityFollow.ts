import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { ActivityReadingPosition } from "@/lib/types/activity";

const BOTTOM_GAP = 32;

/** 步骤按纵向顺序排列；只测量二分查找路径，避免长过程每次滚动扫描全部布局。 */
function readingPosition(container: HTMLElement, mode: ActivityReadingPosition["mode"]): ActivityReadingPosition {
  if (mode === "follow") return { mode, offset: 0, top: container.scrollTop };
  const top = container.getBoundingClientRect().top;
  const entries = container.querySelectorAll<HTMLElement>("[data-activity-entry]");
  let low = 0;
  let high = entries.length;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (entries[middle]!.getBoundingClientRect().bottom > top) high = middle;
    else low = middle + 1;
  }
  const anchor = entries[low];
  return { mode, runId: anchor?.closest<HTMLElement>("[data-activity-run]")?.dataset.activityRun,
    entryId: anchor?.dataset.activityEntry, offset: anchor ? anchor.getBoundingClientRect().top - top : 0, top: container.scrollTop };
}

function restorePosition(container: HTMLElement, position: ActivityReadingPosition): void {
  const anchor = position.entryId && Array.from(container.querySelectorAll<HTMLElement>("[data-activity-entry]"))
    .find((item) => item.dataset.activityEntry === position.entryId && item.closest<HTMLElement>("[data-activity-run]")?.dataset.activityRun === position.runId);
  container.scrollTop = anchor ? container.scrollTop + anchor.getBoundingClientRect().top - container.getBoundingClientRect().top - position.offset : position.top;
}

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
export function useActivityFollow(revision: string, onPause: () => void, current = true,
  initial?: ActivityReadingPosition, save?: (position: ActivityReadingPosition) => void) {
  const scroll = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const enabled = useRef(!initial || initial.mode === "follow");
  const manual = useRef(initial?.mode === "paused");
  const [following, setFollowing] = useState(!initial || initial.mode === "follow");
  const touchY = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const rememberFrame = useRef<number | null>(null);
  const positionRef = useRef(initial);
  const viewport = useRef({ width: 0, height: 0 });
  const restoredTop = useRef<number | null>(null);
  const lastScrollTop = useRef(0);
  const downwardIntent = useRef(false);
  const saveRef = useRef(save);
  useLayoutEffect(() => { saveRef.current = save; }, [save]);
  const remember = useCallback(() => {
    const container = scroll.current;
    if (!container) return;
    positionRef.current = readingPosition(container, enabled.current ? "follow" : manual.current ? "paused" : "reading");
    saveRef.current?.(positionRef.current);
  }, []);
  const initialRef = useRef(initial);
  useLayoutEffect(() => {
    const container = scroll.current;
    const position = initialRef.current;
    if (container && position && position.mode !== "follow") {
      restorePosition(container, position);
      lastScrollTop.current = container.scrollTop;
    }
    if (container) viewport.current = { width: container.clientWidth, height: container.clientHeight };
    return () => {
      if (rememberFrame.current != null) cancelAnimationFrame(rememberFrame.current);
      if (!container) return;
      saveRef.current?.(readingPosition(container, enabled.current ? "follow" : manual.current ? "paused" : "reading"));
    };
  }, []);
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
    remember();
    onPause();
  }, [onPause, cancelFrame, remember]);
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
    remember();
    schedule();
  }, [schedule, remember]);
  useLayoutEffect(schedule, [revision, schedule]);
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      const container = scroll.current;
      if (container) {
        const changed = viewport.current.width !== container.clientWidth || viewport.current.height !== container.clientHeight;
        viewport.current = { width: container.clientWidth, height: container.clientHeight };
        if (changed && !enabled.current && positionRef.current) {
          if (rememberFrame.current != null) cancelAnimationFrame(rememberFrame.current);
          rememberFrame.current = null;
          restorePosition(container, positionRef.current);
          restoredTop.current = container.scrollTop;
          lastScrollTop.current = container.scrollTop;
        }
      }
      schedule();
    });
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
      if (!enabled.current && (viewport.current.width !== element.clientWidth || viewport.current.height !== element.clientHeight
        || restoredTop.current === element.scrollTop)) return;
      const downward = element.scrollTop > lastScrollTop.current;
      lastScrollTop.current = element.scrollTop;
      if (!enabled.current && !manual.current && current && downward && downwardIntent.current
        && element.scrollHeight - element.clientHeight - element.scrollTop <= BOTTOM_GAP) resume();
      if (!enabled.current && rememberFrame.current == null) rememberFrame.current = requestAnimationFrame(() => {
        rememberFrame.current = null;
        remember();
      });
    },
    onWheel: (event: React.WheelEvent<HTMLDivElement>) => {
      restoredTop.current = null;
      downwardIntent.current = event.deltaY > 0 && !consumesScroll(event.target, event.currentTarget, event.deltaY);
      if (event.target instanceof Node && event.currentTarget.contains(event.target) && event.deltaY < 0
        && Math.abs(event.deltaY) >= Math.abs(event.deltaX) && !consumesScroll(event.target, event.currentTarget, event.deltaY)) pauseReading();
    },
    onTouchStart: (event: React.TouchEvent<HTMLDivElement>) => { touchY.current = event.touches[0]?.clientY ?? null; },
    onTouchMove: (event: React.TouchEvent<HTMLDivElement>) => {
      restoredTop.current = null;
      const y = event.touches[0]?.clientY;
      if (y == null || touchY.current == null) return;
      const delta = touchY.current - y;
      if (Math.abs(delta) > 6) {
        downwardIntent.current = delta > 0;
        if (delta < 0 && !consumesScroll(event.target, event.currentTarget, delta)) pauseReading();
        touchY.current = y;
      }
    },
    onTouchEnd: () => { touchY.current = null; },
    onPointerDown: (event: React.PointerEvent<HTMLDivElement>) => {
      const element = event.currentTarget;
      if (event.target === element && event.clientX >= element.getBoundingClientRect().right - (element.offsetWidth - element.clientWidth)) { pauseReading(); downwardIntent.current = true; }
    },
    onKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => {
      restoredTop.current = null;
      if (event.target instanceof Element && event.target.closest("input, textarea, button, a, summary, [contenteditable=true]")) return;
      downwardIntent.current = ["ArrowDown", "PageDown", "End"].includes(event.key);
      if (["ArrowUp", "PageUp", "Home"].includes(event.key) && !consumesScroll(event.target, event.currentTarget, -1)) pauseReading();
    },
  } };
}
