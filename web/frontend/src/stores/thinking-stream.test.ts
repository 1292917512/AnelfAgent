import { afterEach, expect, it, vi } from "vitest";
import { ThinkingStream } from "./thinking-stream";

class FakeSource {
  static CLOSED = 2;
  readyState = 1;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener = vi.fn();
  close = vi.fn();
}
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("marks reconnecting as disconnected, resyncs after reconnect and cancels retry on stop", () => {
  vi.useFakeTimers();
  const sources: FakeSource[] = [];
  vi.stubGlobal("EventSource", class extends FakeSource { constructor() { super(); sources.push(this); } });
  const connected = vi.fn();
  const reconnect = vi.fn();
  const stream = new ThinkingStream({ session_start: vi.fn(), session_end: vi.fn(), node_added: vi.fn(), node_updated: vi.fn(), tools_updated: vi.fn() }, connected, reconnect);
  stream.start();
  sources[0]?.onopen?.();
  expect(reconnect).not.toHaveBeenCalled();
  sources[0]!.readyState = 0;
  sources[0]?.onerror?.();
  expect(connected).toHaveBeenLastCalledWith(false);
  sources[0]?.onopen?.();
  expect(reconnect).toHaveBeenCalledOnce();
  sources[0]!.readyState = FakeSource.CLOSED;
  sources[0]?.onerror?.();
  vi.advanceTimersByTime(5000);
  expect(sources).toHaveLength(2);
  sources[1]?.onopen?.();
  expect(reconnect).toHaveBeenCalledTimes(2);
  sources[1]!.readyState = FakeSource.CLOSED;
  sources[1]?.onerror?.();
  stream.stop();
  vi.advanceTimersByTime(5000);
  expect(sources).toHaveLength(2);
});
