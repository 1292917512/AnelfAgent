import { test, expect } from "./fixtures";
import type { ConversationDiagnostics, DiagnosticRun } from "../src/lib/types";

test("diagnostics keep scopes, receipts and unknown cache distinct", async ({ page }, info) => {
  const runs: DiagnosticRun[] = [
    { id: "web-run", scope: "user_webui:me#a", origin_scope: "user_webui:me#a", parent_id: "", kind: "conversation", trigger: "chat",
      label: "Inspect local search startup", status: "running", started_at: 1000, duration_ms: 12000, step_count: 2, omitted_steps: 0,
      steps: [
        { id: "model", kind: "model", name: "test-model", status: "completed", started_at: 1000, duration_ms: 9000, ttft_ms: 800,
          request_id: "request-a", usage: { total_input_tokens: 10000, completion_tokens: 300, cache_read_input_tokens: 9500, cache_observable: true }, preview: "Generated text, not sent" },
        { id: "tool", kind: "tool", name: "retrieval_services", status: "running", started_at: 1009, duration_ms: 3000, request_id: "request-a", arguments: '{"action":"status"}' },
      ], active_steps: [] },
    { id: "qq-run", scope: "user_qq:other", origin_scope: "user_qq:other", parent_id: "", kind: "conversation", trigger: "chat",
      label: "Check delivery", status: "completed", started_at: 990, duration_ms: 1000, step_count: 2, omitted_steps: 0,
      steps: [
        { id: "unknown-model", kind: "model", name: "no-usage-model", status: "completed", started_at: 990, duration_ms: 800, request_id: "request-b", usage: {} },
        { id: "sent", kind: "tool", name: "send_message", status: "completed", started_at: 991, duration_ms: 20, request_id: "request-b",
          delivery: { mode: "channel", status: "sent", scope: "user_qq:other" }, preview: "Delivered response" },
      ], active_steps: [] },
  ];
  await page.route("**/api/context/recent?**", (route) => {
    const scope = new URL(route.request().url()).searchParams.get("scope");
    const data: ConversationDiagnostics = {
      epoch: "one", observed_at: 1012, collecting: true, current_turn_id: "", retention: { runs: 40, steps_per_run: 80, evicted_runs: 0 },
      matching_runs: 2, active_run_ids: ["web-run"], runs: scope ? runs.filter((run) => run.scope === scope) : runs, scopes: runs.map((run) => run.scope),
      conversations: [{ scope: scope || "user_webui:me#a", loaded: true, messages: [{ id: "12", role: "user", ts: 1000, preview: "Latest question", truncated: false }] }], notes: [],
    };
    return route.fulfill({ json: data });
  });
  await page.goto("/webui/context?tab=diagnostics");
  await expect(page.getByText("Inspect local search startup", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: "Auto refresh" }).uncheck();
  await expect(page.getByText("Generated text, not sent", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /test-model/ }).click();
  const detail = page.getByRole("dialog");
  await expect(detail.getByText("95%", { exact: true })).toBeVisible();
  await expect(detail.getByText("800ms", { exact: true })).toBeVisible();
  await expect(detail.getByText("Generated text, not sent", { exact: true })).toBeVisible();
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByLabel("Conversation scope").selectOption("user_qq:other");
  await expect(page.getByText("Inspect local search startup", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /no-usage-model/ }).click();
  await expect(detail.getByText("—", { exact: true })).toHaveCount(4);
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: /send_message/ }).click();
  await expect(detail.getByText("Delivery receipt · Sent · user_qq:other", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("diagnostic-detail.png") });
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByLabel("Conversation scope").selectOption("");
  await expect(detail).not.toBeVisible();
  await expect(page.getByText("Inspect local search startup", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("diagnostics.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
