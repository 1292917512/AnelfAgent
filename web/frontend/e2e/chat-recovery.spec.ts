import { test, expect, openWebChat, closeWebChatOverlay, installEventStream } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";

test("history code highlights on reveal, including blocks below a collapsed message", async ({ page, context }, info) => {
  await installEventStream(page);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const code = Array.from({ length: 30 }, (_, i) => `value_${i} = ${i}`).join("\n");
  const history = Array.from({ length: 20 }, (_, i) => ({ id: i + 1, role: "assistant", ts: i + 1,
    content: `Example ${i + 1}\n\n\`\`\`python\n${code}\n\`\`\`\n\nHidden example\n\n\`\`\`python\nprint("revealed")\n\`\`\`` }));
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: history }));
  await page.goto("/webui/");
  await openWebChat(page);
  const list = page.locator(".message-list");
  const rows = list.locator("[data-message-key]");
  await expect(rows).toHaveCount(20);
  const latest = rows.last();
  await expect(latest.locator(".shiki")).toHaveCount(1);
  await expect(latest.getByRole("button", { name: "Expand", exact: true })).toBeInViewport();
  await expect(page.getByRole("button", { name: "Back to latest messages", exact: true })).toHaveCount(0);
  await expect(rows.first().locator(".shiki")).toHaveCount(0);
  const plainHeight = await rows.first().locator("pre").first().evaluate((element) => element.getBoundingClientRect().height);
  await expect(latest.locator("pre").last()).toHaveText('print("revealed")');
  await latest.getByRole("button", { name: "Expand", exact: true }).click();
  await latest.locator("pre").last().scrollIntoViewIfNeeded();
  await expect(latest.locator(".shiki")).toHaveCount(2);
  const token = latest.locator(".shiki span[style]").last();
  const lightColor = await token.evaluate((element) => getComputedStyle(element).color);
  await expect(token).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await page.screenshot({ path: info.outputPath("expanded-code-light.png") });
  await page.evaluate(() => { document.documentElement.classList.add("dark"); document.documentElement.dataset.theme = "dark"; });
  await expect.poll(() => token.evaluate((element) => getComputedStyle(element).color)).not.toBe(lightColor);
  await expect(token).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await page.screenshot({ path: info.outputPath("expanded-code-dark.png") });
  await page.evaluate(() => { document.documentElement.classList.remove("dark"); document.documentElement.dataset.theme = "light"; });
  await list.evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));
  await expect(rows.first().locator(".shiki")).toHaveCount(1);
  const highlightedHeight = await rows.first().locator("pre").first().evaluate((element) => element.getBoundingClientRect().height);
  expect(Math.abs(highlightedHeight - plainHeight)).toBeLessThan(1);
  await rows.first().getByRole("button", { name: "Copy", exact: true }).first().click();
  expect((await page.evaluate(() => navigator.clipboard.readText())).replace(/\r\n/g, "\n")).toBe(code);
  await page.screenshot({ path: info.outputPath("visible-code-history.png") });
});

test("chat pages load on demand, preserve the reading anchor and catch up beyond one missed page", async ({ page }) => {
  await installEventStream(page);
  const history = Array.from({ length: 240 }, (_, index) => ({ id: index + 1, role: "assistant", ts: index + 1,
    content: `Message ${index + 1}\n\n${"Historical context. ".repeat(20)}` }));
  const requests: { limit: number; before: number; after: number | null }[] = [];
  await page.route("**/api/chat/history*", (route) => {
    const params = new URL(route.request().url()).searchParams;
    const limit = Number(params.get("limit"));
    const before = Number(params.get("before_id") ?? Infinity);
    const after = params.has("after_id") ? Number(params.get("after_id")) : null;
    requests.push({ limit, before, after });
    const result = after != null ? history.filter((message) => message.id > after).slice(0, limit)
      : history.filter((message) => message.id < before).slice(-limit);
    return route.fulfill({ json: result });
  });
  await page.goto("/webui/");
  await openWebChat(page);
  const list = page.locator(".message-list");
  const rows = list.locator("[data-message-key]");
  await expect(rows).toHaveCount(40);
  await list.evaluate((element) => element.scrollTo({ top: 0, behavior: "instant" }));
  const earlier = list.getByRole("button", { name: "Load earlier messages" });
  await earlier.focus();
  await earlier.click({ trial: true });
  const anchor = rows.first();
  const key = await anchor.getAttribute("data-message-key");
  const anchorOffset = () => list.evaluate((element, anchorKey) => {
    const row = element.querySelector(`[data-message-key="${anchorKey}"]`)!;
    return row.getBoundingClientRect().top - element.getBoundingClientRect().top;
  }, key);
  const top = await anchorOffset();
  await earlier.click();
  await expect(rows).toHaveCount(80);
  await expect.poll(async () => Math.abs(await anchorOffset() - top)).toBeLessThan(8);
  for (let i = 241; i <= 333; i++) history.push({ id: i, role: "assistant", ts: i, content: `Recovered ${i}` });
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "turn_end", data: {} } })));
  await expect(rows).toHaveCount(173);
  await expect.poll(() => requests.some((request) => request.after === 320)).toBe(true);
  await expect.poll(async () => Math.abs(await anchorOffset() - top)).toBeLessThan(8);
  await page.getByRole("button", { name: "Back to latest messages" }).click();
  await expect(rows).toHaveCount(40);
  await expect(list.getByText("Recovered 333", { exact: true })).toBeInViewport();
  expect(requests.every((request) => request.limit === 40)).toBe(true);
  for (let i = 334; i <= 460; i++) history.push({ id: i, role: "assistant", ts: i, content: `Recovered ${i}` });
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "turn_end", data: {} } })));
  await expect(list.getByText("Recovered 460", { exact: true })).toBeAttached();
  await expect(rows).toHaveCount(120);
  await page.screenshot({ path: test.info().outputPath("bounded-chat-history.png") });
});

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
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: { epoch: "recovery", revision: 1, runs: recovered ? [{
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
  await page.getByRole("button", { name: "Synchronize live activity", exact: true }).click();
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(initialConnections + 2);
  await openWebChat(page);
  await expect(input).toHaveValue("Preserve during recovery");
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
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => {
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
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => {
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
