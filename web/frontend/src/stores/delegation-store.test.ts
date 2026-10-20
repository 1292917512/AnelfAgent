import { beforeEach, expect, it } from "vitest";
import { useDelegationStore } from "./delegation-store";
import type { DelegationNode } from "@/lib/types";

const node: DelegationNode = { delegation_id: "active", chat_id: "one", goal: "Work", context_preview: "", role: "leaf", task_index: 0, background: true, depth: 0, status: "running", started_at: 1 };
beforeEach(() => { useDelegationStore.setState({ delegations: {} }); });

it("keeps running tasks when newer completed history reaches the retention limit", () => {
  const store = useDelegationStore.getState();
  store.upsertDelegation(node);
  for (let i = 0; i < 60; i++) store.upsertDelegation({ ...node, delegation_id: `done-${i}`, status: "completed", started_at: i + 2 });
  const nodes = store.getChatDelegations("one");
  expect(nodes).toHaveLength(51);
  expect(nodes.find((item) => item.delegation_id === "active")?.status).toBe("running");
});

it("does not revive a completed run when a delayed started event arrives", () => {
  const store = useDelegationStore.getState();
  store.upsertDelegation(node);
  store.resolveDelegation("one", "active", true, "done");
  store.upsertDelegation(node);
  expect(store.getChatDelegations("one")[0]).toMatchObject({ status: "completed", output: "done" });
});

it("removes missed running cards from an empty recovery snapshot without inferring a result", () => {
  const store = useDelegationStore.getState();
  store.upsertDelegation(node);
  store.upsertDelegation({ ...node, delegation_id: "finished", status: "completed", output: "Evidence" });
  const observed = useDelegationStore.getState().delegations.one;
  store.rehydrate("one", [], observed);
  expect(store.getChatDelegations("one")).toEqual([expect.objectContaining({ delegation_id: "finished", status: "completed", output: "Evidence" })]);
});

it("preserves streamed progress and new delegations received after a recovery request began", () => {
  const store = useDelegationStore.getState();
  store.upsertDelegation(node);
  const observed = useDelegationStore.getState().delegations.one;
  store.updateProgress("one", { chat_id: "one", delegation_id: "active", kind: "round", iteration: 3 });
  store.upsertDelegation({ ...node, delegation_id: "new" });
  store.rehydrate("one", [], observed);
  expect(store.getChatDelegations("one")).toHaveLength(2);
  expect(store.getChatDelegations("one").find((item) => item.delegation_id === "active")?.iteration).toBe(4);
});

it("lets a failed cancellation be retried without reverting a terminal result", () => {
  const store = useDelegationStore.getState();
  store.upsertDelegation(node);
  store.markCancelling("one", "active");
  store.markCancelling("one", "active", false);
  expect(store.getChatDelegations("one")[0]?.cancelling).toBe(false);
  store.resolveDelegation("one", "active", false, "", "", true);
  store.markCancelling("one", "active", false);
  expect(store.getChatDelegations("one")[0]?.status).toBe("cancelled");
});
