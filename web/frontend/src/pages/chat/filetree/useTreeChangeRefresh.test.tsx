import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useTreeChangesStore } from "@/stores/tree-changes-store";
import type { WorkspaceNode } from "@/lib/types";
import { useTreeChangeRefresh } from "./useTreeChangeRefresh";

const fixture = vi.hoisted(() => ({ refresh: vi.fn().mockResolvedValue(undefined), children: [] as WorkspaceNode[] }));
vi.mock("./file-tree-store", () => ({ useFileTreeStore: { getState: () => ({
  trees: { workspace: { children: fixture.children } }, refreshDir: fixture.refresh,
}) } }));

beforeEach(() => { vi.useFakeTimers(); fixture.refresh.mockClear(); fixture.children = []; useTreeChangesStore.getState().clear(); });
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers(); });
const record = (path: string, move_from?: string) => act(() => useTreeChangesStore.getState().record({ path, move_from, diff: "", additions: 1, removals: 0 }));

it("refreshes during continuous traffic and stops pending work when the tree closes", async () => {
  const hook = renderHook(() => useTreeChangeRefresh("workspace"));
  for (let i = 0; i < 20; i++) {
    record(`file-${i}.ts`);
    await act(() => vi.advanceTimersByTimeAsync(100));
  }
  expect(fixture.refresh.mock.calls).toEqual(Array.from({ length: 3 }, () => ["workspace", ""]));
  hook.unmount();
  record("closed.ts");
  await vi.advanceTimersByTimeAsync(1000);
  expect(fixture.refresh).toHaveBeenCalledTimes(3);
});

it("drains all loaded directories in bounded batches and retains a rename source after later edits", async () => {
  fixture.children = Array.from({ length: 12 }, (_, i) => ({ name: `dir-${i}`, path: `dir-${i}`, type: "dir", modified: 0, children: [] }));
  const hook = renderHook(() => useTreeChangeRefresh("workspace"));
  record("dir-1/target.ts", "dir-0/source.ts");
  record("dir-1/target.ts");
  for (let i = 2; i < 12; i++) record(`dir-${i}/file.ts`);
  record("unloaded/file.ts");
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(fixture.refresh).toHaveBeenCalledTimes(8);
  await act(() => vi.advanceTimersByTimeAsync(600));
  expect(fixture.refresh).toHaveBeenCalledTimes(12);
  expect(new Set(fixture.refresh.mock.calls.map((call) => call[1]))).toEqual(new Set(fixture.children.map((dir) => dir.path)));
  hook.unmount();
});
