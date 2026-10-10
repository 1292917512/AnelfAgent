import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ThinkingSession, TraceNode } from "../src/lib/types";

const makeNode = (id: string, type: string, parent_id: string | null = null): TraceNode => ({
  id, type, parent_id, label: id, status: "completed", timestamp: 1770000001, duration_ms: 120, data: {},
});
const trace: ThinkingSession = {
  id: "trace-A", scope: "user_webui:web_user#design", label: "Design review", start_time: 1770000000,
  end_time: 1770000010, duration_ms: 10000, ended: true, is_heartbeat: false, node_count: 7, available_tools: ["read_file", "search"],
  nodes: [
    { ...makeNode("start", "session_start"), data: { scope: "user_webui:web_user#design" } },
    { ...makeNode("context", "context_build"), data: { memory_msgs_count: 5, tool_count: 2 } },
    { ...makeNode("round", "reply_round"), data: { iteration: 0 } },
    { ...makeNode("model", "llm_call", "round"), data: { model: "research-model", tool_calls: ["search"], usage: { prompt_tokens: 1200, completion_tokens: 80, total_tokens: 1280 } } },
    { ...makeNode("tool", "tool_call", "model"), status: "error", data: { tool_name: "search", error: "Search endpoint timed out", arguments: { query: "system architecture" }, result_preview: "Recorded output ".repeat(30) + "END_OF_RESULT" } },
    { ...makeNode("warning", "tool_call", "model"), status: "warning", data: { tool_name: "read_file", result_preview: "Skipped unreadable file" } },
    { ...makeNode("end", "session_end"), label: "Review ended", data: { reason: "completed" } },
  ],
};
const other: ThinkingSession = { ...trace, id: "trace-B", scope: "user_webui:web_user#notes", label: "Notes review", start_time: 1770000005,
  nodes: [{ ...makeNode("B-end", "session_end"), label: "Other run ended" }] };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/thinking/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/status")) await route.fulfill({ json: { enabled: false } });
    else if (path.endsWith("/sessions")) await route.fulfill({ json: { sessions: [trace, other], count: 2 } });
    else if (path.endsWith("/trace-A")) await route.fulfill({ json: trace });
    else if (path.endsWith("/trace-B")) await route.fulfill({ json: other });
    else await route.fulfill({ status: 404, json: { detail: "Trace expired" } });
  });
});

test("trace overview explains work, locates issues and exposes complete recorded data", async ({ page, isMobile }, info) => {
  await page.goto("/webui/thinking");
  // Newest session is selected by timestamp.
  if (isMobile) await page.getByRole("button", { name: "Session List", exact: true }).click();
  await page.getByRole("button").filter({ hasText: "user_webui:web_user#design" }).click();
  const overview = page.getByRole("region", { name: "Run overview" });
  await expect(overview).toContainText("Review ended");
  await expect(overview).toContainText("1,280");
  await page.screenshot({ path: info.outputPath("trace.png") });
  await page.getByRole("button", { name: "2 to inspect" }).click();
  await expect(page.getByRole("heading", { name: "Node details", exact: true, level: 3 })).toBeVisible();
  await expect(page.getByText("system architecture", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/END_OF_RESULT/).first()).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.getByRole("textbox", { name: "Search models, tools or results" }).fill("does-not-exist");
  await expect(page.getByText("No matching events")).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await page.getByRole("button", { name: "Issues", exact: true }).click();
  await expect(page.locator("[data-trace-node]")).toHaveCount(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((violation) => ({ id: violation.id, targets: violation.nodes.map((node) => node.target) }))).toEqual([]);
});

test("trace session errors remain recoverable and graph mode survives reload", async ({ page, isMobile }) => {
  let failing = true;
  await page.route("**/api/thinking/sessions/trace-A", (route) =>
    failing ? route.fulfill({ status: 404, json: { detail: "Trace expired" } }) : route.fulfill({ json: trace }));
  await page.goto("/webui/thinking?view=flow");
  if (isMobile) await page.getByRole("button", { name: "Session List", exact: true }).click();
  await page.getByRole("button").filter({ hasText: "user_webui:web_user#design" }).click();
  await expect(page.getByText("Trace expired")).toBeVisible();
  await expect(page.getByRole("region", { name: "Run overview" })).toHaveCount(0);
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("region", { name: "Run overview" })).toContainText("Review ended");
  await page.reload();
  await expect(page.getByRole("button", { name: "Flow", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("chat drafts stay isolated across conversations and an earlier send acknowledgement", async ({ page }) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/chat/send", async (route) => { await pending; await route.fulfill({ json: { status: "ok" } }); });
  await page.goto("/webui/");
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  await input.fill("Original request");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await input.fill("Next request");
  release();
  await expect(input).toHaveValue("Next request");
  await page.getByRole("button", { name: "New", exact: true }).click();
  await expect(input).toHaveValue("");
  await input.fill("Other conversation");
  await page.getByRole("button", { name: "Default Chat", exact: true }).click();
  await expect(input).toHaveValue("Next request");
});

test("historical traces start at the beginning and provider failures stay inside their panel", async ({ page, isMobile }) => {
  await page.route("**/api/thinking/sessions?*", (route) => route.fulfill({ json: { sessions: [trace], count: 1 } }));
  await page.goto("/webui/thinking");
  await expect(page.locator('[data-trace-node="context"]')).toBeInViewport();
  await page.getByRole("button", { name: "Flow", exact: true }).click();
  const firstNode = page.locator('.react-flow__node[data-id="start"]');
  await expect(firstNode).toBeInViewport();
  await expect.poll(async () => (await firstNode.boundingBox())?.width ?? 0).toBeGreaterThan(140);
  if (isMobile) await page.getByRole("main").getByRole("button", { name: "More", exact: true }).click();
  await page.getByRole("button", { name: "Context Injection", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Context Injection" });
  await expect(dialog.getByRole("alert")).toContainText("Test service unavailable");
  await page.route("**/api/context/providers", (route) => route.fulfill({ json: { total_budget: 1000, current_used: 0, peak_used: 0, providers: [] } }));
  await dialog.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("region", { name: "Run overview" })).toContainText("Review ended");
});
