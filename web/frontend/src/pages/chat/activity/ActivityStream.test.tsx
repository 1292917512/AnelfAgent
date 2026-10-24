import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ActivityRun } from "@/lib/types/activity";
import { activitySegments } from "./activity-stream";
import { ActivityStream } from "./ActivityStream";

const drawEntry = vi.hoisted(() => vi.fn());
vi.mock("./ActivityEntryView", () => ({ ActivityEntryView: () => { drawEntry(); return null; } }));
afterEach(() => { vi.useRealTimers(); });

it("keeps elapsed ticks and metadata-only updates from redrawing the process body", () => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  const run: ActivityRun = { id: "run", revision: 1, scope: "", origin_scope: "", source: { scope: "", channel: "", kind: "", target: "", session: "" },
    actor: "", label: "Inspect", kind: "reflection", input: "", owner_id: "", parent_id: "", started_at: 1, updated_at: 1, ended_at: null,
    status: "running", entry_count: 1, truncated: false, entries: [{ id: "text", kind: "text", ts: 1, content: "Progress" }] };
  const reveal = vi.fn();
  const view = (value: ActivityRun) => <ActivityStream runs={[value]} segments={activitySegments([value])} onReveal={reveal} onToggle={() => {}} />;
  const { rerender } = render(view(run));
  expect(drawEntry).toHaveBeenCalledOnce();
  act(() => { vi.advanceTimersByTime(5000); });
  rerender(view({ ...run, revision: 2, updated_at: 5, entries: [...run.entries] }));
  expect(drawEntry).toHaveBeenCalledOnce();
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(vi.getTimerCount()).toBe(0);
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  expect(vi.getTimerCount()).toBe(1);
});

it("distinguishes completed executions from pending background work and interruptions", () => {
  const run: ActivityRun = { id: "finished", revision: 1, scope: "", origin_scope: "", source: { scope: "", channel: "", kind: "", target: "", session: "" },
    actor: "", label: "Inspect", kind: "reflection", input: "", owner_id: "", parent_id: "", started_at: 1, updated_at: 2, ended_at: 2,
    status: "completed", entry_count: 1, truncated: false, entries: [] };
  const view = (value: ActivityRun) => <ActivityStream runs={[value]} segments={activitySegments([value])} onReveal={() => {}} onToggle={() => {}} />;
  const { rerender } = render(view(run));
  expect(screen.getByRole("status").textContent).toContain("This execution is complete");
  rerender(view({ ...run, entries: [{ id: "queued", kind: "delegation", agent: "Worker", goal: "Review", status: "queued", background: true, ts: 1 }] }));
  expect(screen.getByRole("status").textContent).toContain("Background work pending");
  expect(screen.queryByText("This execution is complete.")).toBeNull();
  rerender(view({ ...run, status: "interrupted" }));
  expect(screen.getByRole("status").textContent).toContain("Interrupted");
  expect(screen.getByRole("status").textContent).not.toContain("Completed");
});
