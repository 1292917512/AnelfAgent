import type { Page } from "@playwright/test";
import { test, expect, installEventStream } from "./fixtures";
import type { ActivityRun, ActivitySnapshot } from "../src/lib/types/activity";

function run(id: string, started: number, patch: Partial<ActivityRun> = {}): ActivityRun {
  return { id, revision: 1, scope: "user_qq:42", origin_scope: "user_qq:42", source: { scope: "user_qq:42", kind: "user", channel: "qq", target: "42", session: "" },
    actor: "", label: `Inspect ${id}`, input: `Inspect ${id}`, kind: "conversation", owner_id: "", parent_id: "", status: "running", started_at: started, updated_at: started, ended_at: null,
    entry_count: 1, truncated: false, entries: [{ id: "thinking", kind: "thinking", ts: started, content: Array.from({ length: 35 }, (_, i) => `Checking item ${i}.`).join("\n\n") }], ...patch };
}

async function liveFixture(page: Page, initial: ActivityRun[]) {
  let snapshot: ActivitySnapshot = { epoch: "follow", revision: 1, runs: initial };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: snapshot }));
  await installEventStream(page);
  await page.addInitScript(() => {
    for (const [name, event] of [["fixture-activity", "activity"], ["fixture-activity-delta", "activity_delta"]]) {
      window.addEventListener(name!, (message) => {
        if (message instanceof CustomEvent) window.dispatchEvent(new CustomEvent("test-activity", { detail: { event, data: message.detail } }));
      });
    }
  });
  return async (value: ActivityRun) => {
    const updated = { ...value, revision: snapshot.revision + 1 };
    snapshot = { ...snapshot, revision: updated.revision, runs: [...snapshot.runs.filter((item) => item.id !== value.id), updated] };
    await page.evaluate((payload) => window.dispatchEvent(new CustomEvent("fixture-activity", { detail: payload })), { epoch: snapshot.epoch, run: updated });
  };
}

test("an open tool inspector receives its timeout and stops counting while the run continues", async ({ page }, info) => {
  await page.clock.install();
  const start = Date.now() / 1000 - 90;
  const tool = { id: "ssh-timeout", kind: "tool" as const, name: "ssh_exec", ts: start, status: "running" as const,
    arguments: '{"command":"powershell -File probe.ps1","timeout":90}', targets: [], request_id: "timeout-request" };
  const active = run("timeout-run", start, { entries: [tool] });
  const publish = await liveFixture(page, [active]);
  await page.goto("/webui/");
  await page.getByRole("button", { name: "View details: ssh_exec", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "ssh_exec", exact: true });
  await expect(dialog.locator(".activity-inspection-meta")).toContainText("Running");
  await publish(active);
  await page.evaluate((payload) => window.dispatchEvent(new CustomEvent("fixture-activity-delta", { detail: payload })), {
    epoch: "follow", base_revision: 2, run: { id: active.id, revision: 3 }, appends: [],
    entries: [{ ...tool, status: "error", result: JSON.stringify({ error: "SSH command timed out", cause: "timeout", retryable: false, outcome: "unknown" }), duration_ms: 90019 }],
  });
  await expect(dialog.locator(".activity-inspection-meta")).toContainText("Failed");
  await expect(dialog.getByRole("region", { name: "Result", exact: true })).toContainText("SSH command timed out");
  await expect(dialog.locator(".activity-inspection-meta")).toContainText("1m 30s");
  await page.clock.fastForward(5000);
  await expect(dialog.locator(".activity-inspection-meta")).toContainText("1m 30s");
  await page.screenshot({ path: info.outputPath("ssh-timeout-result.png"), animations: "disabled" });
});

test("recollection window separates conversations and observes background results without rendering closed details", async ({ page }, info) => {
  const start = Date.now() / 1000 - 10;
  const hook: ActivityRun["entries"][number] = { id: "recall", kind: "hook", name: "memory.probe", event: "context_refresh", mode: "async", source: "internal", scope: "user_qq:42", status: "running", ts: start, duration_ms: 0, detail: "Searching current deployment evidence" };
  let first = run("qq-recall", start, { label: "Investigate deployment", entries: [hook] });
  const second = run("web-recall", start + 1, { label: "Check project memory", scope: "user_webui:1#test", source: { scope: "user_webui:1#test", kind: "user", channel: "webui", target: "1", session: "test" }, entries: [
    { id: "context", kind: "context", status: "done", ts: start + 1, duration_ms: 100, block_count: 1, blocks: [{ layer: "context", label: "Project memory", content: "Separate Web conversation evidence" }] },
  ] });
  const publish = await liveFixture(page, [first, second]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  await expect(page.getByRole("dialog", { name: "Recollection", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Recollection", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Recollection", exact: true });
  await expect(dialog.getByRole("heading", { name: "Investigate deployment", exact: true })).toBeVisible();
  await expect(dialog.getByRole("heading", { name: "Check project memory", exact: true })).toBeVisible();
  await expect(dialog.getByText("Searching current deployment evidence", { exact: true })).toHaveCount(0);
  const qq = dialog.locator("section").filter({ has: page.getByRole("heading", { name: "Investigate deployment", exact: true }) });
  await qq.getByRole("button", { expanded: false }).click();
  await expect(qq).toContainText("Searching current deployment evidence");
  first = { ...first, updated_at: start + 2, entries: [{ ...hook, status: "done", duration_ms: 420, detail: "Ready for the next model call" },
    { id: "injected", kind: "context", status: "done", ts: start + 2, duration_ms: 0, block_count: 1, blocks: [{ layer: "provider", label: "Recollection supplement", content: "[skill:deploy-project] Check deployment steps" }], skills: [{ name: "deploy-project", description: "Deployment checks", mode: "candidate" }] },
  ] };
  await publish(first);
  await expect(qq).toContainText("Ready for the next model call");
  await qq.locator(".activity-context button[aria-expanded]").click();
  await expect(qq.getByText("Suggested", { exact: true })).toBeVisible();
  await qq.locator("summary").filter({ hasText: "Recollection supplement" }).click();
  await expect(qq.getByRole("link", { name: "deploy-project", exact: true }).last()).toHaveAttribute("href", "/webui/skills?skill=deploy-project");
  await expect(qq).not.toContainText("Separate Web conversation evidence");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const bounds = await dialog.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(11);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width - 11);
  await page.screenshot({ path: info.outputPath("recollection.png"), animations: "disabled" });
  await dialog.getByRole("button", { name: "Close details", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".activity-feed")).toHaveAttribute("data-following", "true");
  expect(errors).toEqual([]);
});

test("stage navigation previews without mounting details and pauses following on explicit navigation", async ({ page, isMobile }, info) => {
  const start = Date.now() / 1000 - 30;
  await liveFixture(page, [run("stages", start, { entries: [
    { id: "first-model", kind: "model", name: "reasoning-model", status: "done", generation: 1, ts: start },
    { id: "first-thought", kind: "thinking", content: "Earlier private reasoning", generation: 1, ts: start + 1 },
    { id: "tool", kind: "tool", name: "ssh_exec", status: "done", arguments: "{}", targets: [], request_id: "r", ts: start + 2 },
    { id: "live-thought", kind: "thinking", ts: start + 3, generation: 2, content: Array.from({ length: 40 }, (_, i) => `Live thought ${i}`).join("\n\n") },
  ] })]);
  await page.goto("/webui/");
  const rail = page.getByRole("navigation", { name: "Execution stages" });
  await expect(rail.getByRole("button")).toHaveCount(3);
  const stage = rail.getByRole("button", { name: /Model reasoning · reasoning-model/ });
  if (!isMobile) {
    await stage.hover();
    await expect(page.locator(".activity-stage-preview")).toContainText("reasoning-model");
    await expect(page.getByText("Earlier private reasoning", { exact: true })).toHaveCount(0);
  }
  await stage.click();
  await expect(page.locator(".activity-feed")).toHaveAttribute("data-following", "false");
  await expect(page.getByRole("button", { name: /^reasoning-model/ })).toBeInViewport();
  await expect(stage).toHaveAttribute("aria-current", "step");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("stage-navigation.png"), animations: "disabled" });
  await page.getByRole("button", { name: "Follow paused", exact: true }).click();
  await expect(page.getByText(/Live thought 39/)).toBeInViewport();
});

test("long process content stays within the timeline while wide tables scroll locally", async ({ page }, info) => {
  const start = Date.now() / 1000 - 20;
  const token = "long_identifier_".repeat(50);
  await liveFixture(page, [run("width", start, { label: token, source: { scope: "user_qq:42", channel: "qq", kind: "user", target: token, session: token }, entries: [
    { id: "model", kind: "model", name: token, status: "running", ts: start, generation: 1 },
    { id: "thought", kind: "thinking", content: `Reasoning [skill:${token}]\n${token}`, ts: start + 1, generation: 1 },
    { id: "text", kind: "text", content: `| ${Array(8).fill("Column").join(" | ")} |\n| ${Array(8).fill("---").join(" | ")} |\n| ${Array(8).fill(token).join(" | ")} |\n\n\`\`\`sh\necho ${token}\n\`\`\``, ts: start + 2 },
    { id: "finished-model", kind: "model", name: token, status: "done", ts: start + 3 },
  ] })]);
  await page.goto("/webui/");
  const feed = page.locator(".activity-feed");
  await expect(feed.locator(".markdown-table")).toBeVisible();
  expect(await feed.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await feed.evaluate((element) => { element.scrollLeft = 300; });
  expect(await feed.evaluate((element) => element.scrollLeft)).toBe(0);
  const table = feed.locator(".markdown-table");
  expect(await table.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("timeline-width.png"), animations: "disabled" });
});

test("side rail separates records from running work and removes completed items live", async ({ page, isMobile }, info) => {
  if (!isMobile) await page.setViewportSize({ width: 1680, height: 960 });
  const start = Date.now() / 1000 - 30;
  let parent = run("owner", start, { status: "completed", ended_at: start + 1, label: "Deployment request", entries: [
    { id: "bg", kind: "background", task_kind: "image", description: "Analyze the screenshot", status: "running", ts: start + 1 },
    { id: "old-bg", kind: "background", task_kind: "tool", description: "Already finished", status: "done", ts: start },
    { id: "delegate", kind: "delegation", agent: "Reviewer", goal: "Review deployment", status: "running", run_id: "reviewer", background: true, ts: start + 1 },
    { id: "queued", kind: "delegation", agent: "Queued worker", goal: "Wait", status: "queued", background: true, ts: start + 1 },
  ] });
  const reviewer = run("reviewer", start + 1, { kind: "delegation", owner_id: "delegate", parent_id: parent.id, actor: "Reviewer", label: "Review deployment", entries: [] });
  const task = run("maintenance", start + 2, { kind: "task", label: "Check service health", entries: [] });
  const publish = await liveFixture(page, [parent, reviewer, task, run("latest", start + 3, { label: "Separate conversation", entries: [] })]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  const rail = page.getByRole("complementary", { name: "Execution details" });
  await expect(rail.getByRole("button")).toHaveCount(5);
  expect(await rail.getByRole("button").evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label"))))
    .toEqual(["Recollection", "Decisions", "Running tasks", "Running subagents", "Background tasks"]);
  const background = rail.getByRole("button", { name: "Background tasks", exact: true });
  await expect(background.locator(".activity-side-count")).toHaveText("1");
  expect(await background.evaluate((element) => element.getBoundingClientRect().right <= element.closest("aside")!.getBoundingClientRect().right)).toBe(true);
  expect(await background.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await background.click();
  const bgDialog = page.getByRole("dialog", { name: "Background tasks", exact: true });
  await expect(bgDialog).toContainText("Analyze the screenshot");
  await expect(bgDialog).not.toContainText("Already finished");
  await expect(bgDialog).not.toContainText("Review deployment");
  parent = { ...parent, entries: parent.entries.map((entry) => entry.id === "bg" && entry.kind === "background" ? { ...entry, status: "done", duration_ms: 500 } : entry) };
  await publish(parent);
  await expect(bgDialog).toContainText("Nothing running in this category");
  await expect(bgDialog).not.toContainText("Analyze the screenshot");
  await bgDialog.getByRole("button", { name: "Close details" }).click();
  const agents = rail.getByRole("button", { name: "Running subagents", exact: true });
  await expect(agents.locator(".activity-side-count")).toHaveText("2");
  await agents.click();
  const agentDialog = page.getByRole("dialog", { name: "Running subagents", exact: true });
  await expect(agentDialog).toContainText("Queued worker");
  await agentDialog.getByRole("button", { name: /Reviewer.*Review deployment/ }).click();
  const execution = page.getByRole("dialog", { name: "View execution details", exact: true });
  await expect(execution).toContainText("Review deployment");
  await expect(agentDialog).toHaveCount(0);
  await execution.getByRole("button", { name: "Close", exact: true }).click();
  await publish({ ...reviewer, status: "completed", ended_at: start + 20 });
  await expect(agents.locator(".activity-side-count")).toHaveText("1");
  parent = { ...parent, entries: parent.entries.map((entry) => entry.id === "queued" && entry.kind === "delegation" ? { ...entry, status: "done" } : entry) };
  await publish(parent);
  await expect(agents.locator(".activity-side-count")).toBeEmpty();
  const tasks = rail.getByRole("button", { name: "Running tasks", exact: true });
  await tasks.click();
  const taskDialog = page.getByRole("dialog", { name: "Running tasks", exact: true });
  await expect(taskDialog).toContainText("Check service health");
  await expect(taskDialog).not.toContainText("Separate conversation");
  expect(await taskDialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("running-work.png"), animations: "disabled" });
  await publish({ ...task, status: "completed", ended_at: start + 20 });
  await expect(taskDialog).toContainText("Nothing running in this category");
  expect(errors).toEqual([]);
});

test("live process follows streamed content and concurrent tools until explicitly scrolled", async ({ page, isMobile }, info) => {
  const start = Date.now() / 1000 - 20;
  let current = run("current", start);
  const publish = await liveFixture(page, [current]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  const feed = page.locator(".activity-feed");
  const card = page.locator('[data-activity-run="current"]');
  await expect(feed).toHaveAttribute("data-following", "true");
  await expect(card.locator(".activity-thought button[aria-expanded]")).toHaveAttribute("aria-expanded", "true");
  await expect.poll(() => card.locator(".activity-thought-content").evaluate((element) => element.scrollHeight - element.clientHeight)).toBeLessThan(2);
  await expect.poll(() => feed.evaluate((element) => element.scrollTop)).toBeGreaterThan(100);
  const initialScroll = await feed.evaluate((element) => element.scrollTop);

  current = { ...current, updated_at: start + 1, entries: current.entries.map((entry) => entry.kind === "thinking" ? { ...entry, content: entry.content + "\n\n" + "New evidence arrived.\n\n".repeat(30) + "LATEST-THOUGHT" } : entry) };
  await publish(current);
  await expect(card.getByText(/LATEST-THOUGHT/)).toBeVisible();
  await expect.poll(() => feed.evaluate((element) => element.scrollTop)).toBeGreaterThan(initialScroll + 200);
  await expect(feed).toHaveAttribute("data-following", "true");

  if (isMobile) {
    await feed.dispatchEvent("touchstart", { touches: [{ identifier: 1, clientX: 120, clientY: 200 }] });
    await feed.dispatchEvent("touchmove", { touches: [{ identifier: 1, clientX: 120, clientY: 350 }] });
    await feed.dispatchEvent("touchend", { changedTouches: [{ identifier: 1, clientX: 120, clientY: 350 }] });
  } else { await feed.hover(); await page.mouse.wheel(0, -300); }
  await expect(feed).toHaveAttribute("data-following", "false");
  await feed.evaluate((element) => { element.scrollTop = 240; });
  current = { ...current, updated_at: start + 2, entries: [...current.entries, { id: "tool", kind: "tool", name: "read_memory_file", status: "done", ts: start + 2, arguments: '{"path":"memory/notes.md"}', targets: [{ key: "path", value: "memory/notes.md", path: "config/memory/notes.md", root: "project" }], request_id: "request-follow", result: "Memory inspection completed.", duration_ms: 1240 }] };
  await publish(current);
  await expect(card.getByRole("button", { name: "View details: read_memory_file", exact: true })).toContainText("Completed");
  await expect(card.locator(".activity-tool-detail")).toHaveCount(0);
  await expect.poll(() => feed.evaluate((element) => element.scrollTop)).toBe(240);
  await page.getByRole("button", { name: "Follow paused", exact: true }).click();
  await expect(card.getByText("Memory inspection completed.", { exact: true })).toBeInViewport();
  await expect(card.locator(".activity-thought button[aria-expanded]")).toHaveAttribute("aria-expanded", "true");

  const inspect = card.getByRole("button", { name: "View details: read_memory_file", exact: true });
  await inspect.click();
  await page.getByRole("dialog", { name: "read_memory_file", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
  await expect(feed).toHaveAttribute("data-following", "true");
  const child = run("child", start + 3, { parent_id: current.id, kind: "delegation", actor: "Reviewer", updated_at: start + 3 });
  await publish(child);
  await expect(page.locator('[data-activity-run="child"] .activity-thought-content')).toBeInViewport();
  current = { ...current, updated_at: start + 4, entries: [...current.entries, { id: "final", kind: "text", ts: start + 4, content: "PARENT-FINISHED-STEP" }] };
  await publish(current);
  await expect(card.getByText("PARENT-FINISHED-STEP")).toBeInViewport();
  await expect(feed).toHaveAttribute("data-following", "true");

  // Late layout changes must follow even when no new SSE revision arrives.
  await card.locator(".activity-output").evaluate((element) => { element.setAttribute("style", "padding-top: 350px"); });
  await expect(card.getByText("PARENT-FINISHED-STEP")).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("follow-light.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("follow-dark.png"), animations: "disabled" });
  expect(errors).toEqual([]);
});

test("terminal progress stops live indicators after a long stream and keeps tool details usable", async ({ page }, info) => {
  const start = Date.now() / 1000 - 30;
  const entries: ActivityRun["entries"] = Array.from({ length: 60 }, (_, i) => ({
    id: `tool-${i}`, kind: "tool", ts: start + i / 100, name: "inspect", status: "done", duration_ms: 10,
    arguments: "{}", targets: [], request_id: `request-${i}`, result: `Result ${i}: ${"verified ".repeat(60)}`,
  }));
  const current = run("stream", start, { entries: [...entries,
    { id: "model", kind: "model", ts: start + 1, name: "Test model", status: "running" },
    { id: "thought", kind: "thinking", ts: start + 2, content: "Reviewing results" },
  ] });
  const publish = await liveFixture(page, [current]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  const card = page.locator('[data-activity-run="stream"]');
  await expect(card.locator(".activity-model .animate-spin")).toHaveCount(1);
  await expect(card.locator(".activity-model-toggle")).toHaveAttribute("aria-expanded", "true");
  await expect(card.locator(".activity-thought")).toHaveCount(0);
  for (let i = 0; i < 12; i++) {
    await publish({ ...current, entries: current.entries.map((entry) => entry.kind === "thinking" ? { ...entry, content: "Reviewing results\n".repeat(i + 1) } : entry) });
  }
  await publish({ ...current, status: "completed", ended_at: start + 25, entries: current.entries.map((entry) => entry.kind === "model" ? { ...entry, status: "done", duration_ms: 24000, ttft_ms: 2000,
    usage: { total_input_tokens: 1000, completion_tokens: 250, cache_read_input_tokens: 950, cache_observable: true } } : entry) });
  await expect(page.locator(".activity-connection")).not.toContainText("Working");
  await expect(card.locator(".activity-live-dot, .animate-spin, .activity-thought.is-live")).toHaveCount(0);
  await expect(card.locator(".activity-run-footer")).toContainText("Completed");
  await expect(card.locator(".activity-run-footer")).toBeInViewport();
  await expect(card.locator(".activity-duration")).toHaveText("25s");
  await expect(page.locator(".activity-feed")).toHaveAttribute("data-following", "true");
  await expect(card.locator(".activity-model-toggle")).toHaveAttribute("aria-expanded", "false");
  await expect(card.locator(".activity-thought-content")).toHaveCount(0);
  await card.locator(".activity-model-toggle").click();
  await expect(card.locator(".activity-thought-content")).toContainText("Reviewing results");
  await card.getByRole("button", { name: /View details: Test model/ }).click();
  const thinking = page.getByRole("dialog", { name: /Test model/ });
  await expect(thinking).toContainText("Before first output 2s");
  await expect(thinking).toContainText("After first output 22s");
  await expect(thinking).toContainText("Cache read 950 tokens · 95%");
  await page.screenshot({ path: info.outputPath("model-timing.png"), animations: "disabled" });
  await expect(thinking).toContainText("Reviewing results");
  await thinking.getByRole("button", { name: "Close", exact: true }).click();
  await card.locator(".activity-model-toggle").click();
  await card.getByRole("button", { name: "View details: inspect", exact: true }).last().click();
  const dialog = page.getByRole("dialog", { name: "inspect", exact: true });
  await expect(dialog).toContainText("Result 59");
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("completed-stream.png"), animations: "disabled" });
  expect(errors).toEqual([]);
});

test("current steps stay open until the round becomes history and delivery receipts remain inspectable", async ({ page }) => {
  const start = Date.now() / 1000 - 30;
  const current = run("first", start, { status: "completed", ended_at: start + 5, updated_at: start + 5, entries: [
    { id: "thought", kind: "thinking", ts: start, content: "First round reasoning" },
    { id: "delivery", kind: "notice", origin: "delivery", iteration: 2, ts: start + 5, count: 1, deferred: 0, items: [{ source: { scope: "user_qq:42", channel: "qq", kind: "user", target: "42", session: "" }, role: "system", message_id: "", ts: start + 5, content: "[delivery_mode:auto][delivery_status:sent][delivery_scope:user_qq:42] Reply sent through the shared channel pipeline." }] },
  ] });
  const publish = await liveFixture(page, [current]);
  await page.goto("/webui/");
  const first = page.locator('[data-activity-run="first"]');
  await expect(first.locator(".activity-thought button[aria-expanded]")).toHaveAttribute("aria-expanded", "true");
  await first.getByRole("button", { name: "View details: Reply delivery", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Reply delivery", exact: true });
  await expect(dialog).toContainText("Reply sent through the shared channel pipeline.");
  await expect(dialog.locator('[title="delivery_mode: auto"]')).toBeVisible();
  await expect(dialog.locator('[title="delivery_status: sent"]')).toBeVisible();
  await expect(dialog.locator('[title="delivery_scope: user_qq:42"]')).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await publish(run("second", start + 10));
  await expect(first).toHaveCount(0);
  await page.getByRole("button", { name: "Scroll up for previous round", exact: true }).click();
  await expect(first.locator(".activity-run-heading")).toHaveAttribute("aria-expanded", "false");
  await first.locator(".activity-run-heading").click();
  await expect(first.locator(".activity-thought button[aria-expanded]")).toHaveAttribute("aria-expanded", "false");
  await page.getByRole("button", { name: "Follow paused", exact: true }).click();
  await expect(page.locator('[data-activity-run="second"] .activity-thought button[aria-expanded]')).toHaveAttribute("aria-expanded", "true");
});

test("concurrent channels keep tool ownership, terminal receipts and subagent ancestry readable", async ({ page }, info) => {
  const start = Date.now() / 1000 - 12;
  const qq = run("qq", start, { entries: [{ id: "shell", kind: "tool", owner: "entity:filesystem", name: "run_shell_command", arguments: JSON.stringify({ command: "printf '# literal <b>output</b>'", run_in_background: false }), targets: [], request_id: "local-request", ts: start + 1, status: "done", duration_ms: 140, result: JSON.stringify({ ok: true, returncode: 0, stdout: "# literal <b>output</b>", stderr: "", cwd: "/workspace", shell_cwd: "/workspace/build" }) }] });
  const web = run("web", start + 2, { scope: "user_webui:web_user#one", source: { scope: "user_webui:web_user#one", channel: "webui", target: "web_user", session: "one", kind: "user" }, entries: [{ id: "web-text", kind: "text", ts: start + 3, content: "WEB-CHANNEL-PROGRESS" }] });
  const child = run("review", start + 4, { parent_id: qq.id, kind: "delegation", actor: "Remote reviewer", entries: [{ id: "ssh", kind: "tool", owner: "entity:ssh", name: "ssh_exec", arguments: JSON.stringify({ name: "production", command: "journalctl --no-pager -n 10", work_dir: "/srv/app" }), targets: [], request_id: "remote-request", ts: start + 5, status: "error", duration_ms: 2400, result: JSON.stringify({ exit_code: 2, stdout: "", stderr: "service: unavailable", connection: "production", work_dir: "/srv/app", hint: "Verify the service name" }) }] });
  const publish = await liveFixture(page, [qq, web, child]);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  await expect(page.locator('.tool-command-summary').filter({ hasText: "Exit 0" })).toBeVisible();
  await expect(page.locator('[data-activity-run="qq"] .tool-terminal')).toHaveCount(0);
  await page.getByRole("button", { name: "View details: run_shell_command", exact: true }).click();
  const shellDialog = page.getByRole("dialog", { name: "run_shell_command", exact: true });
  await expect(shellDialog.locator(".tool-terminal code").last()).toContainText("# literal <b>output</b>");
  await expect(shellDialog.locator(".tool-terminal b")).toHaveCount(0);
  await shellDialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator('[data-activity-run="review"]')).toContainText("SSH · production");
  await expect(page.locator('[data-activity-run="review"]')).toContainText("Exit 2");
  await expect(page.locator('.activity-segment-parent')).toContainText("Inspect qq");
  const initialOrder = await page.locator("[data-activity-entry]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-activity-entry")));
  await publish({ ...qq, updated_at: start + 9, entries: qq.entries.map((entry) => entry.kind === "tool" ? { ...entry, result: '{"ok":true,"returncode":0,"stdout":"LATE RESULT"}' } : entry) });
  await expect(page.locator('[data-activity-run="review"] .tool-command-summary')).toBeInViewport();
  expect(await page.locator("[data-activity-entry]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-activity-entry")))).toEqual(initialOrder);
  const sessions = page.getByRole("navigation", { name: "Filter conversations" });
  await sessions.getByRole("button", { name: /webui/ }).click();
  await expect(page.getByText("WEB-CHANNEL-PROGRESS", { exact: true })).toBeVisible();
  await expect(page.locator('[data-activity-run="review"]')).toHaveCount(0);
  await sessions.getByRole("button", { name: "All conversations" }).click();
  await expect(page.locator('[data-activity-run="review"]')).toBeVisible();
  await page.locator('[data-activity-run="review"]').getByRole("button", { name: "View details: ssh_exec", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "ssh_exec", exact: true });
  await expect(dialog.locator(".tool-terminal").filter({ hasText: "service: unavailable" })).toBeVisible();
  await dialog.getByText("Arguments and full receipt", { exact: true }).click();
  await expect(dialog.getByRole("region", { name: "Arguments", exact: true })).toContainText('"work_dir": "/srv/app"');
  await expect(dialog.getByText(/remote-request/)).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("concurrent-tools.png"), animations: "disabled" });
  expect(errors).toEqual([]);
});


test("round summary isolates conversations and opens nested agents and plans on demand", async ({ page }, info) => {
  const start = Date.now() / 1000 - 20;
  let parent = run("qq-round", start, { trigger: "chat", initiator: { id: "7", name: "Operator" }, label: "Check deployment", entries: [
    { id: "review", kind: "delegation", goal: "Review deployment", agent: "Reviewer", status: "running", background: true, run_id: "review-run", ts: start + 1 },
    { id: "queued", kind: "delegation", goal: "Check release notes", agent: "Release checker", status: "queued", background: true, ts: start + 2 },
    { id: "plan", kind: "plan", goal: "Validate deployment", status: "running", ts: start, steps: [{ content: "Inspect configuration", status: "completed", note: "" }, { content: "Check outcome", status: "in_progress", note: "" }] },
  ] });
  const child = run("review-run", start + 1, { kind: "delegation", parent_id: parent.id, owner_id: "review", actor: "Reviewer", label: "Review deployment" });
  const nested = run("nested-run", start + 2, { kind: "delegation", parent_id: child.id, owner_id: "nested", actor: "Auditor", label: "Inspect permissions" });
  const web = run("web-round", start + 3, { scope: "user_webui:web_user#web", origin_scope: "user_webui:web_user#web", source: { scope: "user_webui:web_user#web", kind: "user", channel: "webui", target: "web_user", session: "web" }, label: "Separate Web question", initiator: { id: "web_user", name: "Web user" } });
  const publish = await liveFixture(page, [parent, child, nested, web]);
  await page.goto("/webui/");
  const header = page.locator(".activity-heading");
  await expect(header.locator(".activity-round-focus")).toContainText("Separate Web question");
  await expect(header.getByRole("button", { name: "View details: Round subagents", exact: true })).toHaveCount(0);
  const families = page.getByRole("navigation", { name: "Filter conversations", exact: true });
  await families.getByRole("button", { name: /^qq/ }).click();
  await expect(header.locator(".activity-round-focus")).toContainText("Operator");
  await expect(header.locator(".activity-round-focus")).toContainText("Check deployment");
  const agents = header.getByRole("button", { name: "View details: Round subagents", exact: true });
  await expect(agents).toHaveText("3");
  await expect(page.locator(".activity-summary-list")).toHaveCount(0);
  await agents.click();
  const dialog = page.getByRole("dialog", { name: "Round subagents", exact: true });
  await expect(dialog.locator(".activity-summary-delegate")).toHaveCount(3);
  await expect(dialog).toContainText("Release checker");
  await expect(dialog).toContainText("Waiting to start");
  const auditor = dialog.locator(".activity-summary-delegate").filter({ hasText: "Auditor" });
  await expect(auditor).toContainText("Review deployment");
  await auditor.getByRole("button", { name: "View execution", exact: true }).click();
  const execution = page.getByRole("dialog", { name: "View execution details", exact: true });
  await expect(execution).toContainText("Inspect permissions");
  await execution.getByRole("button", { name: "Close", exact: true }).click();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator(".activity-summary-list")).toHaveCount(0);
  const plans = header.getByRole("button", { name: "View details: Execution plans", exact: true });
  await expect(plans).toHaveText("1/2");
  await plans.click();
  const planDialog = page.getByRole("dialog", { name: "Execution plans", exact: true });
  await expect(planDialog).toContainText("Inspect configuration");
  await expect(planDialog).toContainText("Check outcome");
  await planDialog.getByRole("button", { name: "Close", exact: true }).click();
  parent = { ...parent, updated_at: start + 4, entries: parent.entries.map((entry) => entry.kind === "plan" ? { ...entry, status: "completed", steps: entry.steps.map((step) => ({ ...step, status: "completed" })) } : entry) };
  await publish(parent);
  await expect(plans).toHaveText("2/2");
  expect(await header.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("round-summary-dark.png"), animations: "disabled" });
  await families.getByRole("button", { name: /^webui/ }).click();
  await expect(header.locator(".activity-round-focus")).toContainText("Separate Web question");
  await expect(agents).toHaveCount(0);
  await expect(plans).toHaveCount(0);
});
