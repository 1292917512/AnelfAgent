import AxeBuilder from "@axe-core/playwright";
import { test, expect, openWebChat, closeWebChatOverlay } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";

function activity(id: string, patch: Partial<ActivityRun> = {}): ActivityRun {
  return {
    id, revision: 1, scope: "group_qq:42", origin_scope: "group_qq:42", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" },
    actor: "", label: "Inspect deployment and report the service status", kind: "conversation", input: "[channel:qq][group_id:42][name:Operator] Inspect deployment and report the service status", owner_id: "", parent_id: "", status: "running", started_at: Date.now() / 1000 - 24, updated_at: Date.now() / 1000, ended_at: null, entry_count: 5, truncated: false,
    entries: [
      { id: "llm", kind: "model", name: "review-model", status: "done", duration_ms: 2300, ts: Date.now() / 1000 - 24 },
      { id: "thought", kind: "thinking", content: "Check [channel:qq] and [group_id:42] before reporting the deployment result.", ts: Date.now() / 1000 - 24 },
      { id: "tool", kind: "tool", name: "inspect_service", arguments: '{"target":"group_qq:42","path":"note.txt"}', targets: [{ key: "target", value: "group_qq:42" }, { key: "path", value: "note.txt" }], request_id: "request-review-1", status: "done", result: "Service is healthy. No restart needed.", duration_ms: 1280, ts: Date.now() / 1000 - 20 },
      { id: "plan", kind: "plan", goal: "Check the deployment", status: "running", ts: Date.now() / 1000 - 18, steps: [{ content: "Inspect service status", status: "completed", note: "Healthy" }, { content: "Validate settings", status: "in_progress", note: "" }] },
      { id: "delegation", kind: "delegation", goal: "Validate deployment settings", agent: "Reviewer", status: "running", background: true, run_id: "child", ts: Date.now() / 1000 - 12 },
    ], ...patch,
  };
}

test("context evidence, tagged results and cache facts remain readable on narrow screens", async ({ page }) => {
  const run = activity("evidence", { entries: [
    { id: "context", kind: "context", status: "done", duration_ms: 2300, ts: Date.now() / 1000, block_count: 1,
      blocks: [{ layer: "memory", label: "Matched memory and skill candidates", content: "[group_id:42][topic:review] Evidence BLUE-42; candidate skill review-check." }] },
    { id: "model", kind: "model", name: "review-model", status: "done", duration_ms: 8100, ts: Date.now() / 1000,
      usage: { total_input_tokens: 12000, completion_tokens: 300, cache_observable: false, cache_read_input_tokens: 0 } },
    { id: "receipt", kind: "tool", name: "read_file", arguments: '{"file_path":"review/task-a.txt"}', targets: [], request_id: "request-42", status: "error", duration_ms: 12, ts: Date.now() / 1000,
      result: JSON.stringify({ error: "Missing file for [group_id:42]", hint: "Verify the path before retrying.", diagnostic: { request_id: "request-42" } }) },
  ] });
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "test", revision: 2, runs: [run] } }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await expect(execution.getByText("Cache not reported", { exact: true })).toBeVisible();
  await expect(execution.getByText("Cached 0%", { exact: true })).toHaveCount(0);
  await execution.getByRole("button", { name: "Memory and context 2s", exact: true }).click();
  await execution.locator(".activity-context-detail summary").click();
  await expect(execution.getByText("Evidence BLUE-42", { exact: false })).toBeVisible();
  await expect(execution.locator('[title="topic: review"]')).toBeVisible();
  await expect(execution.getByText("Verify the path before retrying.", { exact: true })).toBeVisible();
  await execution.getByText("Full receipt", { exact: true }).click();
  await expect(execution.getByRole("region", { name: "Result", exact: true })).toContainText("request-42");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include(".activity-pane").analyze();
  expect(accessibility.violations).toEqual([]);
});

test("workspace shows the global live process beside actual Web messages", async ({ page, isMobile }, info) => {
  const parent = activity("global");
  const child = activity("child", { parent_id: "global", kind: "delegation", owner_id: "delegation", actor: "Reviewer", label: "Validate deployment settings", input: "Validate deployment settings", entries: [
    { id: "child-thought", kind: "thinking", content: "Reviewing settings while the main conversation continues.", ts: Date.now() / 1000 - 10 },
    { id: "child-tool", kind: "tool", name: "read_deployment_settings", status: "running", ts: Date.now() / 1000 - 8, arguments: '{"path":"note.txt"}', targets: [{ key: "path", value: "note.txt" }], request_id: "child-request" },
  ] });
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "test", revision: 2, runs: [parent, child] } }));
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: [{ id: 1, role: "assistant", content: "Your Web message was received.", ts: Date.now() / 1000,
    thinking: "Internal Web reasoning stays outside this pane", toolCalls: [{ call_id: "old", name: "old_web_tool", status: "done" }] }] }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await expect(execution).toBeVisible();
  await expect(execution.getByRole("tab")).toHaveCount(0);
  await expect(execution.getByRole("button", { name: "Start Tracking", exact: true })).toHaveCount(0);
  await expect(execution.getByText("read_deployment_settings", { exact: true })).toBeVisible();
  await expect(execution.getByText("Group 42", { exact: true }).first()).toBeVisible();
  const activeRuns = execution.getByRole("navigation", { name: "Active runs", exact: true });
  await expect(activeRuns.getByRole("button")).toHaveCount(2);
  await activeRuns.getByRole("button", { name: "qq · Inspect deployment and report the service status", exact: true }).click();
  await expect(execution.getByRole("button", { name: "Jump to latest", exact: true })).toBeVisible();
  const parentCard = execution.locator('[data-activity-run="global"]');
  await parentCard.getByRole("button", { name: "Thought", exact: true }).click();
  await expect(parentCard.getByText("before reporting the deployment result.", { exact: false })).toBeVisible();
  await parentCard.getByRole("button", { name: /inspect_service Completed/ }).click();
  await expect(parentCard.getByRole("region", { name: "Result", exact: true })).toContainText("Service is healthy");
  await expect(parentCard.getByText("Call ID: tool", { exact: false })).toBeVisible();
  await expect(parentCard.getByText("Validate settings", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("workspace-live-activity.png") });
  const childCard = execution.locator('[data-activity-run="child"]');
  await childCard.locator(".activity-run-heading").click();
  await expect(childCard.getByText("read_deployment_settings", { exact: true })).toHaveCount(0);
  await parentCard.getByRole("button", { name: "View execution", exact: true }).click();
  await expect(childCard).toBeInViewport();
  await expect(childCard.getByText("read_deployment_settings", { exact: true })).toBeVisible();
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("workspace-live-activity-dark.png"), animations: "disabled" });
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  await expect(chat.getByText("Your Web message was received.", { exact: true })).toBeVisible();
  await expect(chat.getByText("old_web_tool", { exact: true })).toHaveCount(0);
  await expect(chat.getByText("Internal Web reasoning stays outside this pane", { exact: true })).toHaveCount(0);
  await expect(chat.getByText("read_deployment_settings", { exact: true })).toHaveCount(0);
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep this Web draft");
  await closeWebChatOverlay(page);
  await page.screenshot({ path: info.outputPath("workspace-paired-panes.png") });
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }))).toEqual([]);
  await openWebChat(page);
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep this Web draft");
  if (isMobile) await expect(page.getByRole("dialog", { name: "Web chat", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("global activity snapshot failures are retryable without hiding Web messages", async ({ page }) => {
  let failed = true;
  await page.route("**/api/workspace/activity", (route) => failed
    ? route.fulfill({ status: 503, json: { detail: "Activity temporarily unavailable" } })
    : route.fulfill({ json: { epoch: "test", revision: 1, runs: [activity("recovered")] } }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await expect(execution.getByRole("alert")).toContainText("Activity temporarily unavailable");
  failed = false;
  await execution.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(execution.getByText("inspect_service", { exact: true })).toBeVisible();
});

test("long execution keeps its heading fixed while tools are opened", async ({ page }) => {
  const run = activity("long", { entries: Array.from({ length: 24 }, (_, index) => ({
    id: `tool-${index}`, kind: "tool", name: `read_file_${index}`, status: "done", ts: Date.now() / 1000,
    arguments: '{"path":"note.txt"}', targets: [{ key: "path", value: "note.txt" }],
    result: "File content", duration_ms: 10, request_id: `request-${index}`,
  })) });
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "test", revision: 1, runs: [run] } }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await execution.getByRole("button", { name: "read_file_23 Completed · 10ms", exact: true }).click();
  const geometry = await execution.evaluate((element) => ({
    height: element.clientHeight, content: element.scrollHeight,
    top: element.getBoundingClientRect().top,
    heading: element.querySelector("h2")?.getBoundingClientRect().top ?? -1,
    parentScroll: element.parentElement?.scrollTop ?? -1,
  }));
  expect(geometry.content).toBeLessThanOrEqual(geometry.height + 1);
  expect(geometry.heading).toBeGreaterThanOrEqual(geometry.top);
  expect(geometry.parentScroll).toBe(0);
});

test("only the current round mounts until the user scrolls into transient history", async ({ page, isMobile }) => {
  const old = activity("old", { label: "Earlier completed request", status: "completed", started_at: 10, ended_at: 20, updated_at: 20, entries: [] });
  const current = activity("current", { label: "Current live request", started_at: 30, entries: [] });
  let restarted = false;
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: {
    epoch: restarted ? "restarted" : "test", revision: 2, runs: restarted ? [] : [old, current],
  } }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await expect(execution.locator('[data-activity-run="current"]')).toBeVisible();
  await expect(execution.locator('[data-activity-run="old"]')).toHaveCount(0);
  const feed = execution.locator(".activity-feed");
  if (isMobile) {
    await feed.dispatchEvent("touchstart", { touches: [{ identifier: 1, clientX: 100, clientY: 180 }] });
    await feed.dispatchEvent("touchend", { changedTouches: [{ identifier: 1, clientX: 100, clientY: 300 }] });
  } else await feed.dispatchEvent("wheel", { deltaY: -120 });
  await expect(execution.locator('[data-activity-run="old"]')).toBeVisible();
  await expect(execution.locator('[data-activity-run="current"]')).toHaveCount(0);
  await execution.getByRole("button", { name: "Jump to latest", exact: true }).click();
  await expect(execution.locator('[data-activity-run="current"]')).toBeVisible();
  await expect(execution.locator('[data-activity-run="old"]')).toHaveCount(0);
  restarted = true;
  await page.reload();
  await expect(execution.locator("[data-activity-run]")).toHaveCount(0);
});

test("overview keeps maintenance separate and metric geometry aligned", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  await page.route("**/api/status/", (route) => route.fulfill({ json: { ready: true, status: { uptime: 3673, mind_phase: "idle", message_count: 162 } } }));
  await page.route("**/api/status/pfc", (route) => route.fulfill({ json: { pending_messages: [], general_tasks: [], short_term_memory_count: 3, short_term_memory_max: 10, tool_recall: [], tag_activated_tools: [], active_tools: [] } }));
  await page.route("**/api/tools/", (route) => route.fulfill({ json: [{ name: "inspect_service", enabled: true }] }));
  await page.route("**/api/status/components", (route) => route.fulfill({ json: { structured: { ready: true, llm: { impl: "LLMManager", model: "review-model" }, storage: { impl: "DataCenter", sqlite: "ready" }, tools: { total: 913, enabled: 858, by_source: { core: 20, entity: 838 } } } } }));
  await page.route("**/api/mcp/", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/status/services", (route) => route.fulfill({ json: { services: [] } }));
  await page.route("**/api/status/startup", (route) => route.fulfill({ json: { timeline: [] } }));
  await page.route("**/api/status/events", (route) => route.fulfill({ json: { stats: {} } }));
  await page.goto("/webui/dashboard");
  await expect(page.getByRole("button", { name: "Restart Service", exact: true })).toHaveCount(0);
  const cells = page.locator(".dashboard-metrics > div");
  await expect(cells).toHaveCount(7);
  const tops = await cells.evaluateAll((elements) => elements.map((element) => Math.round(element.getBoundingClientRect().top)));
  expect(tops[0]).toBe(tops[1]);
  await page.screenshot({ path: info.outputPath("overview-dark.png") });
  await page.getByRole("button", { name: "Service management", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Service management", exact: true });
  await expect(dialog.getByRole("button", { name: "Restart Service", exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Update, Build & Restart", exact: true })).toBeVisible();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(dialog).toHaveCSS("animation-name", "none");
  await page.screenshot({ path: info.outputPath("overview-maintenance.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
