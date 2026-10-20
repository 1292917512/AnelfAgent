import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useActivityFollow } from "./useActivityFollow";

let resize = () => {};
let nextFrame = 0;
const frames = new Map<number, FrameRequestCallback>();
const paused = vi.fn();
beforeEach(() => {
  frames.clear(); paused.mockClear();
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => { frames.set(++nextFrame, callback); return nextFrame; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => frames.delete(id));
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resize = callback; }
    observe() {} disconnect() {}
  });
});
afterEach(() => { vi.unstubAllGlobals(); });

function Feed({ revision = "1", current = true }: { revision?: string; current?: boolean }) {
  const { scroll, content, following, pause, resume, handlers } = useActivityFollow(revision, paused, current);
  return <><button onClick={pause}>Pause</button><button onClick={resume}>Resume</button>
    <span>{following ? "following" : "reading"}</span>
    <div data-testid="feed" ref={scroll} {...handlers}><div ref={content}><pre data-testid="code" style={{ overflowY: "auto" }}>Code</pre></div></div></>;
}

function geometry(element: HTMLElement, height: number, viewport: number, top: number) {
  Object.defineProperties(element, { scrollHeight: { configurable: true, value: height }, clientHeight: { configurable: true, value: viewport } });
  element.scrollTop = top;
}
function flush() {
  act(() => { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach((callback) => callback(0)); });
}

it("follows growth, ignores downward and nested scrolling, and resumes after reading back to the bottom", () => {
  const { rerender } = render(<Feed />);
  const feed = screen.getByTestId("feed");
  geometry(feed, 1200, 200, 0);
  flush();
  expect(feed.scrollTop).toBe(1000);
  fireEvent.wheel(feed, { deltaY: 50 });
  const code = screen.getByTestId("code");
  geometry(code, 600, 100, 100);
  fireEvent.wheel(code, { deltaY: -50 });
  expect(screen.getByText("following")).toBeTruthy();
  geometry(feed, 1800, 200, 1000);
  act(resize); flush();
  expect(feed.scrollTop).toBe(1600);
  fireEvent.wheel(feed, { deltaY: -50 });
  fireEvent.scroll(feed);
  expect(screen.getByText("reading")).toBeTruthy();
  feed.scrollTop = 1300;
  fireEvent.scroll(feed);
  rerender(<Feed revision="2" />); flush();
  expect(feed.scrollTop).toBe(1300);
  expect(paused).toHaveBeenCalledOnce();
  fireEvent.wheel(feed, { deltaY: 100 });
  feed.scrollTop = 1600;
  fireEvent.scroll(feed); flush();
  expect(screen.getByText("following")).toBeTruthy();
});

it("cancels queued alignment for user reading and preserves explicit pause and historical navigation", () => {
  const { rerender } = render(<Feed />);
  const feed = screen.getByTestId("feed");
  geometry(feed, 1200, 200, 500);
  fireEvent.wheel(feed, { deltaY: -40 }); flush();
  expect(feed.scrollTop).toBe(500);
  rerender(<Feed current={false} />);
  feed.scrollTop = 1000; fireEvent.scroll(feed);
  expect(screen.getByText("reading")).toBeTruthy();
  fireEvent.click(screen.getByText("Resume")); flush();
  fireEvent.click(screen.getByText("Pause"));
  rerender(<Feed revision="3" />); fireEvent.scroll(feed); act(resize); flush();
  expect(screen.getByText("reading")).toBeTruthy();
});
