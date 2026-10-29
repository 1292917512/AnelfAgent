import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
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

it("updates limit guidance when the terminal configuration source changes", () => {
  const run: ActivityRun = { id: "limited", revision: 1, scope: "", origin_scope: "", source: { scope: "", channel: "", kind: "", target: "", session: "" },
    actor: "", label: "Inspect", kind: "reflection", input: "", owner_id: "", parent_id: "", started_at: 1, updated_at: 2, ended_at: 2,
    status: "budget_exhausted", entry_count: 0, truncated: false, entries: [], limit: { kind: "iterations", value: 8, config_keys: ["max_tool_iterations"] } };
  const view = (value: ActivityRun) => <MemoryRouter><ActivityStream runs={[value]} segments={activitySegments([value])} onReveal={() => {}} onToggle={() => {}} /></MemoryRouter>;
  const { rerender } = render(view(run));
  expect(screen.getByRole("alert").textContent).toContain("8-round limit");
  rerender(view({ ...run, revision: 2, limit: { kind: "iterations", value: 12, config_keys: ["delegation_max_iterations_cap"] } }));
  expect(screen.getByRole("alert").textContent).toContain("12-round limit");
  expect(screen.getByRole("link").getAttribute("href")).toBe("/config?key=delegation_max_iterations_cap");
});
