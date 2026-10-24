import type { Page } from "@playwright/test";
import { test, expect, installEventStream, openWebChat, closeWebChatOverlay } from "./fixtures";
import type { ActivityRun, ActivitySnapshot } from "../src/lib/types/activity";

function run(id: string, patch: Partial<ActivityRun> = {}): ActivityRun {
  return { id, revision: 1, kind: "maintenance", scope: "", origin_scope: "", parent_id: "", owner_id: "", actor: "", label: id, input: "",
    source: { channel: "", kind: "", target: "", session: "", scope: "" },
    status: "running", started_at: 1, updated_at: 1, ended_at: null, entry_count: 0, truncated: false, entries: [], ...patch };
}

async function emit(page: Page, event: string, data: unknown = {}) {
  await page.evaluate((detail) => window.dispatchEvent(new CustomEvent("test-activity", { detail })), { event, data });
}

test("idle runtime discovers Cognee maintenance without relying on a busy phase or Web submission", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  let snapshots = 0;
  const snapshot: ActivitySnapshot = { epoch: "maintenance", revision: 0, runs: [] };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => { snapshots++; return route.fulfill({ json: snapshot }); });
  await page.goto("/webui/");
  await expect(page.getByText("No execution records yet", { exact: true })).toBeVisible();
  const baseline = snapshots;
  const connections = await page.evaluate(() => Number(Reflect.get(window, "testStreamConnections")));
  snapshot.revision = 1;
  snapshot.runs = [run("Cognee", { entries: [{ id: "compact", kind: "phase", name: "cognee_compact", ts: 1, status: "running", duration_ms: 0 }] })];
  await emit(page, "ping");
  await page.clock.runFor(40_100);
  await expect(page.getByRole("button", { name: "View details: Cognee storage maintenance", exact: true })).toBeVisible();
  expect(snapshots).toBe(baseline + 1);
  expect(await page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(connections);
});

test("busy cross-channel streaming cannot mask a lost child terminal and snapshots remain bounded", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  let snapshots = 0;
  const child = run("Worker", { kind: "delegation", parent_id: "parent", owner_id: "delegate", entries: [{ id: "child-text", kind: "text", ts: 2, content: "Child investigation" }] });
  const channel = run("Other channel", { kind: "conversation", source: { channel: "qq", kind: "group", target: "42", session: "", scope: "group_qq:42" },
    entries: [{ id: "streaming", kind: "text", ts: 3, content: "Cross-channel progress" }] });
  const parent = run("parent", { kind: "conversation", status: "completed", ended_at: 2,
    entries: [{ id: "queued", kind: "delegation", ts: 1, goal: "Queued review", agent: "Reviewer", status: "queued", background: true }] });
  const snapshot: ActivitySnapshot = { epoch: "active", revision: 1, runs: [parent, child, channel] };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => { snapshots++; return route.fulfill({ json: snapshot }); });
  await page.goto("/webui/");
  await expect(page.locator('[data-activity-run="Worker"] .activity-run-heading')).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("Child investigation", { exact: true })).toHaveCount(0);
  const baseline = snapshots;
  const connections = await page.evaluate(() => Number(Reflect.get(window, "testStreamConnections")));
  Object.assign(child, { revision: 2, status: "completed", ended_at: 4 });
  snapshot.revision = 2;
  for (let second = 0; second < 45; second++) {
    channel.revision = ++snapshot.revision;
    await emit(page, "activity", { epoch: snapshot.epoch, run: channel });
    await page.clock.runFor(1000);
  }
  expect(snapshots).toBe(baseline + 1);
  await page.getByRole("button", { name: "Running subagents", exact: true }).click();
  const children = page.getByRole("dialog", { name: "Running subagents", exact: true });
  await expect(children.getByText("Worker", { exact: true })).toHaveCount(0);
  await children.getByRole("button", { name: "View details: Reviewer", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Reviewer", exact: true }).getByText("Queued review", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(connections);
});

test("reconnect restores compression and child state and removes missed legacy running cards", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  let snapshots = 0;
  let delegationRequests = 0;
  const child = run("Recovered child", { kind: "delegation", owner_id: "worker" });
  const compact = run("Compression", { entries: [{ id: "phase", kind: "phase", name: "cognee_compact", ts: 1, status: "running", duration_ms: 0 }] });
  const snapshot: ActivitySnapshot = { epoch: "before", revision: 1, runs: [child, compact] };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => { snapshots++; return route.fulfill({ json: snapshot }); });
  await page.route("**/api/chat/delegations*", (route) => { delegationRequests++; return route.fulfill({ json: { running: [] } }); });
  await page.goto("/webui/");
  await openWebChat(page);
  await expect.poll(() => delegationRequests).toBe(1);
  await emit(page, "delegation_started", { chat_id: "default", delegation_id: "worker", goal: "Recovered child", background: true });
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await closeWebChatOverlay(page);
  const baseline = snapshots;
  const connections = await page.evaluate(() => Number(Reflect.get(window, "testStreamConnections")));
  await emit(page, "test-disconnect");
  snapshot.epoch = "after";
  snapshot.revision = 1;
  snapshot.runs = [run("Compression restored", { status: "completed", ended_at: 3,
    entries: [{ id: "phase", kind: "phase", name: "cognee_compact", ts: 1, status: "done", duration_ms: 2000, detail: "Storage maintenance finished" }] })];
  await page.clock.runFor(5100);
  await expect(page.getByText("Storage maintenance finished", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Recovered child", exact: true })).toHaveCount(0);
  expect(snapshots).toBe(baseline + 1);
  await expect.poll(() => delegationRequests).toBe(2);
  await openWebChat(page);
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(connections + 1);
});
