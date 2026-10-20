import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { EventStream } from "./event-stream";

class Source extends EventTarget {
  static CLOSED = 2;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
}
const sources: Source[] = [];
let stream: EventStream;
let received = () => {};
const changed = vi.fn();
const resync = vi.fn();
const healthCheck = vi.fn();

beforeEach(() => {
  vi.useFakeTimers(); sources.length = 0; changed.mockClear(); resync.mockClear(); healthCheck.mockClear();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.stubGlobal("EventSource", class extends Source { constructor() { super(); sources.push(this); } });
  stream = new EventStream("/events", (_source, _current, _recover, pulse) => { received = pulse; }, changed, vi.fn(), resync, healthCheck);
});
afterEach(() => { stream.stop(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("recovers silent connections once with bounded backoff and ignores old frames", () => {
  stream.start(); sources[0]?.onopen?.();
  const oldPulse = received;
  vi.advanceTimersByTime(90_000);
  expect(changed).toHaveBeenLastCalledWith(false, "stalled");
  expect(sources[0]?.close).toHaveBeenCalledOnce();
  oldPulse();
  expect(changed).toHaveBeenLastCalledWith(false, "stalled");
  vi.advanceTimersByTime(5000);
  expect(sources).toHaveLength(2);
  sources[1]?.onopen?.();
  expect(changed).toHaveBeenLastCalledWith(true, "connected");
  stream.stop();
  vi.advanceTimersByTime(200_000);
  expect(sources).toHaveLength(2);
  expect(vi.getTimerCount()).toBe(0);
});

it("counts heartbeat and business frames without notifying subscribers on every frame", () => {
  stream.start(); sources[0]?.onopen?.(); changed.mockClear();
  for (let index = 0; index < 8; index++) {
    vi.advanceTimersByTime(30_000);
    if (index % 2) received(); else sources[0]?.dispatchEvent(new MessageEvent("ping"));
  }
  expect(sources[0]?.close).not.toHaveBeenCalled();
  expect(changed).not.toHaveBeenCalled();
});

it("allows background suspension and grants a fresh receive window when visible again", () => {
  stream.start(); sources[0]?.onopen?.();
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  vi.advanceTimersByTime(300_000);
  expect(healthCheck).not.toHaveBeenCalled();
  expect(sources[0]?.close).not.toHaveBeenCalled();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(resync).toHaveBeenCalledOnce();
  vi.advanceTimersByTime(80_000);
  expect(sources[0]?.close).not.toHaveBeenCalled();
  sources[0]?.dispatchEvent(new MessageEvent("ping"));
  vi.advanceTimersByTime(20_000);
  expect(sources).toHaveLength(1);
});
