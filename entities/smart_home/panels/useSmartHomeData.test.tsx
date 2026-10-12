import type { PropsWithChildren } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { smartHomeApi } from "./api";
import { useSmartHomeData } from "./useSmartHomeData";

vi.mock("./api", () => ({ smartHomeApi: {
  status: vi.fn(), devices: vi.fn(), domains: vi.fn(), preview: vi.fn(), control: vi.fn(),
} }));
class Stream extends EventTarget {
  static current: Stream;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor() { super(); Stream.current = this; }
}
const state = { entity_id: "light.one", name: "Lamp", domain: "light", state: "on", area: "Room", available: true, stale: false, provider: "ha", updated_at: 1, attributes: {}, actions: {} };
beforeEach(() => {
  vi.stubGlobal("EventSource", Stream);
  vi.mocked(smartHomeApi.status).mockResolvedValue({ data: { providers: [], connected: true } } as Awaited<ReturnType<typeof smartHomeApi.status>>);
  vi.mocked(smartHomeApi.devices).mockResolvedValue({ data: { devices: [state], count: 1 } } as Awaited<ReturnType<typeof smartHomeApi.devices>>);
  vi.mocked(smartHomeApi.domains).mockResolvedValue({ data: { domains: [] } } as Awaited<ReturnType<typeof smartHomeApi.domains>>);
  vi.mocked(smartHomeApi.preview).mockResolvedValue({ data: { content: "", injecting: false } } as Awaited<ReturnType<typeof smartHomeApi.preview>>);
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: PropsWithChildren) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return renderHook(useSmartHomeData, { wrapper });
}
it("upserts newly discovered devices and closes the stream on unmount", async () => {
  const hook = setup();
  await waitFor(() => expect(hook.result.current.devices).toHaveLength(1));
  act(() => Stream.current.dispatchEvent(new MessageEvent("state", { data: JSON.stringify({ device: { ...state, entity_id: "light.new" } }) })));
  await waitFor(() => expect(hook.result.current.devices).toHaveLength(2));
  expect(smartHomeApi.devices).toHaveBeenCalledTimes(1);
  hook.unmount();
  expect(Stream.current.close).toHaveBeenCalledOnce();
});
it("a missing primary query shows an error even when the device list succeeded", async () => {
  vi.mocked(smartHomeApi.status).mockRejectedValue(new Error("offline"));
  const hook = setup();
  await waitFor(() => expect(hook.result.current.isError).toBe(true));
});
it("prevents duplicate commands and preserves failure feedback", async () => {
  let reject: (reason: Error) => void = () => {};
  vi.mocked(smartHomeApi.control).mockImplementation(() => new Promise((_resolve, rejectPromise) => { reject = rejectPromise; }));
  const hook = setup();
  await waitFor(() => expect(hook.result.current.devices).toHaveLength(1));
  await act(async () => {
    const first = hook.result.current.control("light.one", "turn_off");
    expect(await hook.result.current.control("light.one", "turn_off")).toBe(false);
    reject(new Error("command rejected"));
    expect(await first).toBe(false);
  });
  expect(smartHomeApi.control).toHaveBeenCalledTimes(1);
  expect(hook.result.current.pendingIds.size).toBe(0);
  expect(hook.result.current.feedback["light.one"].message).toBe("command rejected");
});
