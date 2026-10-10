import AxeBuilder from "@axe-core/playwright";
import { test, expect, openWebChat, closeWebChatOverlay } from "./fixtures";
import type { ThinkingSession } from "../src/lib/types";

const trace: ThinkingSession = {
  id: "qq-run", label: "Review deployment", scope: "group_qq:42", start_time: 1770000000,
  ended: true, end_time: 1770000010, duration_ms: 10000, outcome: "completed", is_heartbeat: false,
  node_count: 3, available_tools: ["inspect_service"], nodes: [
    { id: "start", type: "session_start", label: "Deployment review", timestamp: 1770000000, duration_ms: null, status: "completed", parent_id: null, data: { scope: "group_qq:42" } },
    { id: "model", type: "llm_call", label: "Analyze", timestamp: 1770000001, duration_ms: 400, status: "completed", parent_id: null,
      data: { model: "review-model", reasoning_content: "Check the deployment status before changing any service configuration.", usage: { prompt_tokens: 14000, completion_tokens: 200, cache_read_input_tokens: 12000 } } },
    { id: "tool", type: "tool_call", label: "Inspect", timestamp: 1770000002, duration_ms: 120, status: "completed", parent_id: "model",
      data: { tool_name: "inspect_service", result_preview: "Service is healthy. No restart needed." } },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/thinking/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/status")) return route.fulfill({ json: { enabled: false } });
    if (path.endsWith("/sessions")) return route.fulfill({ json: { sessions: [trace], count: 1 } });
    return route.fulfill({ json: trace });
  });
  await page.route("**/api/delegations/overview", (route) => route.fulfill({ json: { running: [
    { delegation_id: "worker", goal: "Review service health and validate the deployment configuration", agent: "reviewer", model: "fast-model", role: "leaf", scope: "group_qq:42", chat_id: "", state: "running", background: true, task_index: 0, started_at: Date.now() / 1000 - 20, elapsed_seconds: 20, iteration: 2, current_tool: "inspect_service", usage: { input_tokens: 2400, output_tokens: 300, turns: 2 } },
    { delegation_id: "queued", goal: "Prepare the final report", agent: "writer", role: "leaf", scope: "user_webui:web_user#default", chat_id: "default", state: "queued", background: false, task_index: 1, started_at: Date.now() / 1000 - 2, elapsed_seconds: 2, iteration: 0, current_tool: "", usage: {} },
  ] } }));
});

test("workspace separates global execution, subagents and the Web conversation", async ({ page, isMobile }, info) => {
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: [{ id: 1, role: "assistant", content: "Deployment looks healthy.", ts: 1770000000,
    thinking: "Verify the Web request separately", toolCalls: [{ call_id: "web-tool", name: "check_web_request", status: "done", duration_ms: 100, result_preview: "Web operation complete" }] }] }));
  await page.goto("/webui/");
  const execution = page.getByRole("region", { name: "Global execution", exact: true });
  await expect(execution).toBeVisible();
  await expect(execution.getByText("Check the deployment status before changing any service configuration.")).toBeVisible();
  await expect(execution.getByRole("button", { name: /2 sub-agents running/ })).toBeVisible();
  await page.screenshot({ path: info.outputPath("execution-workspace.png") });
  const picker = page.getByRole("button", { name: "Session List", exact: true });
  const overlay = await picker.isVisible();
  if (overlay) await picker.click();
  const sessions = overlay ? page.getByRole("dialog", { name: "Session List", exact: true }) : execution.locator("aside").first();
  await sessions.getByRole("combobox", { name: "Source", exact: true }).selectOption("qq");
  await expect(sessions.getByRole("button").filter({ hasText: "group_qq:42" })).toBeVisible();
  if (overlay) await sessions.getByRole("button", { name: "Close", exact: true }).click();
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  await expect(chat.locator("[data-trace-node]")).toHaveCount(0);
  await expect(chat.getByText("Deployment looks healthy.", { exact: true })).toBeVisible();
  await expect(chat.getByText("check_web_request", { exact: true })).toHaveCount(0);
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep this Web draft");
  await closeWebChatOverlay(page);
  await execution.getByRole("tab", { name: "Web records", exact: true }).click();
  await expect(execution.getByText("check_web_request", { exact: true })).toBeVisible();
  await execution.getByRole("tab", { name: /Sub-agents/ }).click();
  await expect(execution.getByText("Review service health and validate the deployment configuration", { exact: true })).toBeVisible();
  await expect(execution.getByText("Waiting for capacity", { exact: true })).toBeVisible();
  await expect(execution.getByText("qq", { exact: true })).toBeVisible();
  await expect(page.locator(".workbench-toolbar")).toBeInViewport();
  await page.screenshot({ path: info.outputPath("workspace-agents.png") });
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }))).toEqual([]);
  await openWebChat(page);
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep this Web draft");
  if (isMobile) await expect(page.getByRole("dialog", { name: "Web chat", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("agent progress failures stay retryable and cancellation failures retain controls", async ({ page }) => {
  let fail = true;
  await page.route("**/api/delegations/worker/progress*", (route) => fail
    ? route.fulfill({ status: 503, json: { detail: "Progress storage unavailable" } })
    : route.fulfill({ json: { running: true, truncated: false, lines: ["Inspecting service configuration", "Health checks passed"] } }));
  await page.route("**/api/delegations/worker/cancel", (route) => route.fulfill({ json: { status: "error", error: "Cancellation rejected by runtime" } }));
  await page.goto("/webui/");
  await page.getByRole("tab", { name: /Sub-agents/ }).click();
  await page.getByRole("button", { name: "Logs", exact: true }).first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer.getByRole("alert")).toContainText("Progress storage unavailable");
  fail = false;
  await drawer.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(drawer.getByText("Health checks passed", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).first().click();
  await expect(page.getByText("Cancellation rejected by runtime", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop", exact: true }).first()).toBeEnabled();
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
