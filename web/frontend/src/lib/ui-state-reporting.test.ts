import { afterEach, expect, it, vi } from "vitest";
import { AxiosHeaders } from "axios";
import { uiApi } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { startUiStateReporting } from "./ui-state-reporting";

afterEach(() => { vi.restoreAllMocks(); vi.useRealTimers(); });

it("renews presence while idle, reports connection and visibility changes, and cleans up", () => {
  vi.useFakeTimers();
  const report = vi.spyOn(uiApi, "reportState").mockResolvedValue({
    data: {}, status: 200, statusText: "OK", headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() },
  });
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  useChatStore.setState({ streamStatus: "connected" });
  const stop = startUiStateReporting("/");
  expect(report).toHaveBeenLastCalledWith(expect.objectContaining({ connected: true, visible: true, focused: true }));
  vi.advanceTimersByTime(15_000);
  expect(report).toHaveBeenCalledTimes(2);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  document.dispatchEvent(new Event("visibilitychange"));
  expect(report).toHaveBeenLastCalledWith(expect.objectContaining({ visible: false }));
  useChatStore.setState({ streamStatus: "reconnecting" });
  expect(report).toHaveBeenLastCalledWith(expect.objectContaining({ connected: false }));
  stop();
  const calls = report.mock.calls.length;
  vi.advanceTimersByTime(60_000);
  document.dispatchEvent(new Event("visibilitychange"));
  expect(report).toHaveBeenCalledTimes(calls);
});
