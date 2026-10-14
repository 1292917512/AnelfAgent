import type { Page } from "@playwright/test";
import { test, expect } from "./fixtures";
import type { ActivityRun, ActivitySnapshot } from "../src/lib/types/activity";

function run(id: string, started: number, patch: Partial<ActivityRun> = {}): ActivityRun {
  return { id, revision: 1, scope: "user_qq:42", origin_scope: "user_qq:42", source: { scope: "user_qq:42", kind: "user", channel: "qq", target: "42", session: "" },
    actor: "", label: `Inspect ${id}`, input: `Inspect ${id}`, kind: "conversation", owner_id: "", parent_id: "", status: "running", started_at: started, updated_at: started, ended_at: null,
    entry_count: 1, truncated: false, entries: [{ id: "thinking", kind: "thinking", ts: started, content: Array.from({ length: 35 }, (_, i) => `Checking item ${i}.`).join("\n\n") }], ...patch };
}

async function liveFixture(page: Page, initial: ActivityRun[]) {
  let snapshot: ActivitySnapshot = { epoch: "follow", revision: 1, runs: initial };
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: snapshot }));
  await page.addInitScript(() => {
    const Native = window.EventSource;
    const streams = new Set<EventSource>();
    window.EventSource = class extends Native {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        if (String(url).endsWith("/api/chat/stream")) streams.add(this);
      }
      close() { streams.delete(this); super.close(); }
    };
    window.addEventListener("fixture-activity", (event) => {
      if (event instanceof CustomEvent) for (const stream of streams) stream.dispatchEvent(new MessageEvent(event.detail.run.status === "running" ? "activity" : "activity_end", { data: JSON.stringify(event.detail) }));
    });
  });
  return async (value: ActivityRun) => {
    const updated = { ...value, revision: snapshot.revision + 1 };
    snapshot = { ...snapshot, revision: updated.revision, runs: [...snapshot.runs.filter((item) => item.id !== value.id), updated] };
    await page.evaluate((payload) => window.dispatchEvent(new CustomEvent("fixture-activity", { detail: payload })), { epoch: snapshot.epoch, run: updated });
  };
}

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
  for (let i = 0; i < 12; i++) {
    await publish({ ...current, entries: current.entries.map((entry) => entry.kind === "thinking" ? { ...entry, content: "Reviewing results\n".repeat(i + 1) } : entry) });
  }
  await publish({ ...current, status: "completed", ended_at: start + 25, entries: current.entries.map((entry) => entry.kind === "model" ? { ...entry, status: "done", duration_ms: 24000 } : entry) });
  await expect(page.locator(".activity-connection")).not.toContainText("Working");
  await expect(card.locator(".activity-live-dot, .animate-spin, .activity-thought.is-live")).toHaveCount(0);
  await expect(card.locator(".activity-run-footer")).toContainText("Completed");
  await expect(card.locator(".activity-run-footer")).toBeInViewport();
  await expect(card.locator(".activity-duration")).toHaveText("25s");
  await expect(page.locator(".activity-feed")).toHaveAttribute("data-following", "true");
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
