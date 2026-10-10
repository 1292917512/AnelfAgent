import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { devopsApi } from "./api";
import { useDevopsOperation } from "./operations";

vi.mock("./api", () => ({ devopsApi: { restart: vi.fn(), buildAndRestart: vi.fn(), updateAndRestart: vi.fn(), update: vi.fn(), buildState: vi.fn() } }));
const response = (data: object) => Promise.resolve({ data });

beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks(); useDevopsOperation.setState({ phase: "idle", busy: false, detail: "" }); });
afterEach(() => vi.useRealTimers());

describe("shared deployment operations", () => {
  it("does not treat a rejected request as a restart", async () => {
    vi.mocked(devopsApi.restart).mockImplementation(() => response({ ok: false, message: "No supervisor" }) as ReturnType<typeof devopsApi.restart>);
    await useDevopsOperation.getState().run("restart");
    expect(useDevopsOperation.getState()).toMatchObject({ phase: "failed", busy: false, detail: "No supervisor" });
    expect(devopsApi.buildState).not.toHaveBeenCalled();
  });
  it("deduplicates submissions and waits for a new process instead of an old health response", async () => {
    vi.mocked(devopsApi.restart).mockImplementation(() => response({ ok: true, runtime_id: "old" }) as ReturnType<typeof devopsApi.restart>);
    vi.mocked(devopsApi.buildState).mockImplementationOnce(() => response({ runtime_id: "old", restarting: true }) as ReturnType<typeof devopsApi.buildState>)
      .mockImplementationOnce(() => response({ runtime_id: "new", restarting: false }) as ReturnType<typeof devopsApi.buildState>);
    const pending = useDevopsOperation.getState().run("restart");
    await useDevopsOperation.getState().run("update");
    expect(devopsApi.updateAndRestart).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2000);
    expect(useDevopsOperation.getState().phase).toBe("restarting");
    await vi.advanceTimersByTimeAsync(2000);
    await pending;
    expect(useDevopsOperation.getState()).toMatchObject({ phase: "ready", busy: false });
  });
  it("reports a restart refusal after a successful build", async () => {
    vi.mocked(devopsApi.updateAndRestart).mockImplementation(() => response({ ok: true, runtime_id: "old", operation_id: "op" }) as ReturnType<typeof devopsApi.updateAndRestart>);
    vi.mocked(devopsApi.buildState).mockImplementation(() => response({ runtime_id: "old", operation_id: "op", building: false, result: { ok: false, message: "Restart refused" } }) as ReturnType<typeof devopsApi.buildState>);
    const pending = useDevopsOperation.getState().run("update");
    await vi.advanceTimersByTimeAsync(2000);
    await pending;
    expect(useDevopsOperation.getState()).toMatchObject({ phase: "failed", detail: "Restart refused", busy: false });
  });
  it("times out unreachable services without claiming success", async () => {
    vi.mocked(devopsApi.restart).mockImplementation(() => response({ ok: true, runtime_id: "old" }) as ReturnType<typeof devopsApi.restart>);
    vi.mocked(devopsApi.buildState).mockRejectedValue(new Error("offline"));
    const pending = useDevopsOperation.getState().run("restart");
    await vi.advanceTimersByTimeAsync(242000);
    await pending;
    expect(useDevopsOperation.getState()).toMatchObject({ phase: "failed", busy: false });
  });
});
