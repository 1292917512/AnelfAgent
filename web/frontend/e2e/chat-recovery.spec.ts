import { test, expect, openWebChat, closeWebChatOverlay, installEventStream } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";

test("a closed connection recovers missed Web replies and runtime without reloading or resending", async ({ page }) => {
  let recovered = false;
  let messageId = "";
  let submissions = 0;
  let connections = 0;
  await page.route("**/api/chat/stream", (route) => {
    connections++;
    return recovered ? route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" })
      : route.fulfill({ status: 503, body: "Temporary connection loss" });
  });
  await page.route("**/api/chat/send", (route) => {
    const body = route.request().postDataJSON() as { message_id: string };
    messageId = body.message_id;
    submissions++;
    recovered = true;
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: recovered ? [
    { id: 1, cid: messageId, role: "user", content: "Recover this reply", ts: 1 },
    { id: 2, role: "assistant", content: "Reply delivered while connection was closed", ts: 2 },
  ] : [] }));
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "recovery", revision: 1, runs: recovered ? [{
    id: "recovered", revision: 1, kind: "conversation", scope: "", origin_scope: "", parent_id: "", owner_id: "", actor: "", label: "Recovered execution", input: "",
    source: { channel: "webui", kind: "user", target: "web_user", session: "default", scope: "" },
    status: "completed", started_at: 1, updated_at: 2, ended_at: 2, entry_count: 0, truncated: false, entries: [],
  }] : [] } }));
  await page.goto("/webui/");
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Recover this reply");
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep my next draft");
  await expect(chat.getByText("Reply delivered while connection was closed", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(chat.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  await expect(chat.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep my next draft");
  expect(submissions).toBe(1);
  await expect.poll(() => connections, { timeout: 10000 }).toBeGreaterThan(1);
});

test("a silent live stream reports staleness and reconnects without losing the draft", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  await page.goto("/webui/");
  await openWebChat(page);
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  await input.fill("Preserve during recovery");
  await closeWebChatOverlay(page);
  const initialConnections = await page.evaluate(() => Number(Reflect.get(window, "testStreamConnections")));
  await page.clock.runFor(85_000);
  for (let attempt = 0; attempt < 20; attempt++) {
    if ((await page.locator(".activity-connection").textContent())?.includes("No recent live updates")) break;
    await page.clock.runFor(1000);
  }
  await expect(page.getByRole("region", { name: "Global execution", exact: true })).toContainText("No recent live updates");
  await page.clock.runFor(5010);
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(initialConnections + 1);
  await openWebChat(page);
  await expect(input).toHaveValue("Preserve during recovery");
  await closeWebChatOverlay(page);
  await expect(page.getByRole("button", { name: "Synchronize live activity", exact: true })).toBeVisible();
});

test("accepted messages remain visible while global decisions have no execution records", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  let submissions = 0;
  let snapshots = 0;
  let statusUnavailable = false;
  await page.route("**/api/status/", (route) => statusUnavailable ? route.fulfill({ status: 503, json: { detail: "Status unavailable" } })
    : route.fulfill({ json: { ready: true, status: { mind_phase: "deciding", queue_size: 0 } } }));
  await page.route("**/api/status/thinking", (route) => route.fulfill({ json: {
    pending_messages: [{ scope: "user_webui:web_user", adapter_key: "webui", preview: "Check the pending work" }], general_tasks: [],
  } }));
  await page.route("**/api/workspace/activity", (route) => {
    snapshots++;
    return route.fulfill({ json: { epoch: "pending", revision: 0, runs: [] } });
  });
  await page.route("**/api/chat/send", (route) => { submissions++; return route.fulfill({ json: { status: "ok" } }); });
  await page.goto("/webui/");
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Check the pending work");
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  await expect(chat.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep the next draft");
  await expect.poll(() => snapshots).toBeGreaterThan(1);
  await closeWebChatOverlay(page);
  const runtime = page.getByRole("complementary", { name: "Runtime status and pending messages", exact: true });
  await expect(runtime).toContainText("Agent currently: Deciding");
  await expect(runtime).toContainText("1 message conversations");
  await expect(runtime).toContainText("1 Web conversations are awaiting execution feedback");
  const bounds = await runtime.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await page.screenshot({ path: test.info().outputPath("runtime-pending.png") });
  await page.clock.fastForward(130_000);
  await expect.poll(() => snapshots).toBeGreaterThan(2);
  await openWebChat(page);
  await expect(chat.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await expect(chat.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep the next draft");
  expect(submissions).toBe(1);
  await closeWebChatOverlay(page);
  statusUnavailable = true;
  await page.getByRole("button", { name: "Synchronize live activity", exact: true }).click();
  await expect(runtime).toContainText("Runtime status unavailable");
  statusUnavailable = false;
  await page.getByRole("button", { name: "Synchronize live activity", exact: true }).click();
  await expect(runtime).toContainText("Agent currently: Deciding");
});

test("runtime facts recover other channel work when heartbeats arrive but activity frames are missing", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  let started = false;
  let snapshots = 0;
  const run: ActivityRun = {
    id: "other-project", revision: 1, kind: "conversation", scope: "group_qq:42", origin_scope: "", parent_id: "", owner_id: "", actor: "", label: "Inspect other project", input: "",
    source: { channel: "qq", kind: "group", target: "42", session: "", scope: "group_qq:42" }, status: "running", can_stop: true,
    started_at: Date.now() / 1000, updated_at: Date.now() / 1000, ended_at: null, entry_count: 1, truncated: false,
    entries: [{ id: "progress", kind: "text", ts: Date.now() / 1000, content: "Other project progress recovered" }],
  };
  await page.route("**/api/status/", (route) => route.fulfill({ json: { ready: true, status: { mind_phase: started && run.status === "running" ? "llm_calling" : "idle", queue_size: 0 } } }));
  await page.route("**/api/workspace/activity", (route) => {
    snapshots++;
    return route.fulfill({ json: { epoch: "other", revision: started ? run.revision : 0, runs: started ? [run] : [] } });
  });
  await page.goto("/webui/");
  await expect(page.getByText("No execution records yet", { exact: true })).toBeVisible();
  started = true;
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "ping", data: {} } })));
  await page.clock.runFor(5000);
  const runtime = page.getByRole("complementary", { name: "Runtime status and pending messages", exact: true });
  await expect(runtime).toContainText("Agent currently: LLM Calling");
  await page.clock.runFor(35_000);
  await expect(page.getByText("Other project progress recovered", { exact: true })).toBeVisible();
  await expect(runtime).toHaveCount(0);
  const recoveredRequests = snapshots;
  for (let index = 0; index < 20; index++) {
    run.revision++;
    await page.evaluate((frame) => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "activity", data: frame } })), { epoch: "other", run });
    await page.clock.runFor(1000);
  }
  expect(snapshots).toBe(recoveredRequests);
  Object.assign(run, { revision: run.revision + 1, status: "completed", can_stop: false, ended_at: Date.now() / 1000 });
  await page.clock.runFor(15_000);
  await expect(page.getByRole("button", { name: "Stop execution", exact: true })).toHaveCount(0);
  expect(snapshots).toBeGreaterThan(recoveredRequests);
});
