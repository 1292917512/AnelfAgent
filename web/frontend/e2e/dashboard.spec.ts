import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { Page } from "@playwright/test";
import type { DelegationHistoryItem, LogEntry } from "../src/lib/types";

const logEntry = (seq: number, message = `Message ${seq}`, level = "INFO"): LogEntry => ({
  seq, message, level, tag: seq % 2 ? "mind" : "tools", time: "12:00:00", timestamp: 1791604800 + seq,
});

async function logStream(page: Page, entries: LogEntry[]) {
  await page.addInitScript((initial) => {
    const Native = window.EventSource;
    Object.defineProperty(window, "EventSource", { value: function (url: string, options?: EventSourceInit) {
      if (!url.endsWith("/status/logs/stream")) return new Native(url, options);
      const source = Object.assign(new EventTarget(), {
        onerror: null as ((event: Event) => void) | null,
        close: () => window.removeEventListener("test:log-frame", receive),
      });
      function receive(event: Event) {
        const frame = (event as CustomEvent<{ type: string; data: unknown }>).detail;
        if (frame.type === "error") source.onerror?.(new Event("error"));
        else source.dispatchEvent(new MessageEvent(frame.type, { data: JSON.stringify(frame.data) }));
      }
      window.addEventListener("test:log-frame", receive);
      queueMicrotask(() => source.dispatchEvent(new MessageEvent("snapshot", { data: JSON.stringify({ logs: initial }) })));
      return source;
    } });
  }, entries);
}

test("overview prioritizes delegations and makes nested history traceable", async ({ page }, info) => {
  const now = 1791604800;
  let phase = "tool_executing";
  const items: DelegationHistoryItem[] = Array.from({ length: 20 }, (_, i) => ({
    delegation_id: `task-${i}`, goal: `Review deployment ${i}`, scope: "group_qq:42", adapter_key: "qq", model: "review-model",
    status: i % 4 === 0 ? "failed" : "success", started_at: now - 200 - i * 100, finished_at: now - i * 100, duration_seconds: 20 + i * 7,
    parent_id: i === 0 ? "parent-review" : "", depth: i === 0 ? 2 : 0, summary: i === 0 ? "Connection timed out after retries" : "Review completed",
    usage: { turns: 3, input_tokens: 4500, output_tokens: 200 },
  }));
  await page.route("**/api/status/", (route) => route.fulfill({ json: { ready: true, status: { uptime: 1200, mind_phase: phase, message_count: 48 } } }));
  await page.route("**/api/status/pfc", (route) => route.fulfill({ json: { tool_recall: [{ name: "read_file", count: 83 }, { name: "recall", count: 25 }], short_term_memory_count: 2, short_term_memory_max: 10, pending_messages: [], general_tasks: [] } }));
  await page.route("**/api/status/components", (route) => route.fulfill({ json: { structured: { ready: true, tools: { enabled: 123, total: 140, by_source: { core: 80, entity: 60 } }, llm: { impl: "LLMClient", model: "review-model" } } } }));
  await page.route("**/api/mcp/", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/status/services", (route) => route.fulfill({ json: { services: [] } }));
  await page.route("**/api/status/startup", (route) => route.fulfill({ json: { timeline: [] } }));
  await page.route("**/api/status/events", (route) => route.fulfill({ json: { stats: {} } }));
  await page.route("**/api/delegations/history*", (route) => route.fulfill({ json: { items } }));
  await page.route("**/api/delegations/task-0/progress*", (route) => route.fulfill({ json: { delegation_id: "task-0", running: false, truncated: false, lines: ["[12:00:00] Tool started", "[12:00:20] Connection timed out"] } }));
  const requests: string[] = [];
  page.on("request", (request) => requests.push(new URL(request.url()).pathname));
  await page.goto("/webui/dashboard");
  await expect(page.locator(".delegation-history-row")).toHaveCount(6);
  await expect(page.locator('.overview-phase-track [data-active="true"]')).toContainText("Execute");
  phase = "introspecting";
  await expect(page.locator('.overview-phase-track [data-active="true"]')).toContainText("Think", { timeout: 10000 });
  await expect(page.getByRole("group", { name: "Execution durations" }).getByRole("button")).toHaveCount(20);
  expect(requests).not.toContain("/api/tools/");
  expect(requests).not.toContain("/api/status/services");
  await page.getByRole("button", { name: "Issues only", exact: false }).click();
  await expect(page.locator(".delegation-history-row")).toHaveCount(5);
  await page.locator(".delegation-history-row").filter({ hasText: "Review deployment 0" }).click();
  const drawer = page.getByRole("dialog", { name: "Execution details" });
  await expect(drawer.getByText("parent-review", { exact: true })).toBeVisible();
  await expect(drawer.getByText("Connection timed out after retries")).toBeVisible();
  await expect(drawer.getByText("Failed", { exact: true })).toBeVisible();
  await expect(drawer.getByText("3 turns · 4.7k tokens")).toBeVisible();
  await drawer.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Issues only", exact: false }).click();
  await page.getByRole("button", { name: "Show 14 more" }).click();
  await expect(page.locator(".delegation-history-row")).toHaveCount(13);
  await page.getByRole("button", { name: /System details/ }).click();
  await expect(page.getByRole("heading", { name: "System Services", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
  await page.screenshot({ path: info.outputPath("dashboard.png") });
});

test("logs preserve full messages, filter by source and retain data when clearing fails", async ({ page }, info) => {
  const message = "Tool output\n" + "A long diagnostic line. ".repeat(40) + "\nTRACE-END";
  await logStream(page, [logEntry(1, "Ready"), logEntry(2, message, "ERROR"), logEntry(3, "Queued")]);
  await page.route("**/api/status/logs/clear", (route) => route.fulfill({ status: 503, json: { detail: "Storage unavailable" } }));
  await page.goto("/webui/dashboard?tab=logs");
  await expect(page.locator(".log-row")).toHaveCount(3);
  const footer = await page.locator(".logs-console-footer").boundingBox();
  const main = await page.locator("main").boundingBox();
  expect(footer!.y + footer!.height).toBeLessThanOrEqual(main!.y + main!.height);
  const error = page.locator('.log-row[data-level="ERROR"]');
  await expect(error).not.toContainText("TRACE-END");
  await error.getByRole("button", { name: "Show full message" }).click();
  await expect(error).toContainText("TRACE-END");
  await page.getByRole("textbox", { name: /Search keyword/ }).fill("TRACE-END");
  await expect(page.locator(".log-row")).toHaveCount(1);
  await expect(page.locator("mark")).toHaveText("TRACE-END");
  await page.getByRole("button", { name: "Clear search" }).click();
  await error.getByRole("button", { name: "tools", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "All Tags" })).toHaveValue("tools");
  await expect(page.locator(".log-row")).toHaveCount(1);
  await page.getByRole("combobox", { name: "All Tags" }).selectOption("");
  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Clear", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Storage unavailable");
  await expect(page.locator(".log-row")).toHaveCount(3);
  await page.getByRole("dialog").getByRole("button", { name: "Cancel", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").analyze();
  expect(accessibility.violations.filter((item) => ["serious", "critical"].includes(item.impact ?? ""))).toEqual([]);
  await page.screenshot({ path: info.outputPath("logs.png") });
});

test("paused logs retain a bounded burst and show connection recovery", async ({ page }) => {
  await logStream(page, [logEntry(1)]);
  await page.goto("/webui/dashboard?tab=logs");
  await expect(page.locator(".log-row")).toHaveCount(1);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  const burst = Array.from({ length: 2100 }, (_, index) => logEntry(index + 2));
  await page.evaluate((entries) => entries.forEach((data) => window.dispatchEvent(new CustomEvent("test:log-frame", { detail: { type: "log", data } }))), burst);
  await expect(page.getByRole("button", { name: /Resume.*2000/ })).toBeVisible();
  await expect(page.locator(".log-row")).toHaveCount(1);
  await page.getByRole("button", { name: /Resume/ }).click();
  await expect(page.locator(".log-row")).toHaveCount(2000);
  await expect(page.locator(".log-row").first()).toHaveAttribute("data-seq", "102");
  await expect(page.locator(".log-row").last()).toHaveAttribute("data-seq", "2101");
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("test:log-frame", { detail: { type: "error" } })));
  await expect(page.getByText("Log connection lost. Reconnecting automatically.")).toBeVisible();
  await page.evaluate((entries) => window.dispatchEvent(new CustomEvent("test:log-frame", { detail: { type: "snapshot", data: { logs: entries } } })), [logEntry(2101), logEntry(2102)]);
  await expect(page.locator(".log-row")).toHaveCount(2);
  await expect(page.getByText("Log connection lost. Reconnecting automatically.")).toHaveCount(0);
});
