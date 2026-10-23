import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";
import type { HookExecution } from "../src/lib/types/hooks";

const now = Date.now() / 1000;
const hook: HookExecution = { id: "hook-1", task_id: "task-1", name: "skill_review", event: "after_reply", mode: "async", source: "code", scope: "user_qq:42", status: "running", started_at: now - 180, duration_ms: 0, detail: "Reviewing the deployment procedure", phase: "tool", operation: "get_skill", tool_calls: 1, model_calls: 1, activity_ids: ["hook-child"], context_mode: "transcript", context_messages: 109 };
const parent: ActivityRun = { id: "parent", scope: hook.scope, origin_scope: hook.scope, source: { scope: hook.scope, channel: "qq", target: "42", kind: "user", session: "" }, actor: "", label: "Review the deployment", kind: "conversation", input: "Review the deployment", owner_id: "", parent_id: "", status: "completed", started_at: now - 190, ended_at: now - 181, updated_at: now, revision: 1, entry_count: 1, truncated: false, entries: [{ ...hook, ts: hook.started_at, kind: "hook" }] };
const child: ActivityRun = { ...parent, id: "hook-child", parent_id: "parent", owner_id: hook.id, kind: "hook", scope: "reflect:review", label: "skill_review", status: "running", ended_at: null, entry_count: 3, entries: [
  { id: "m1", kind: "model", name: "review-model", status: "done", duration_ms: 15200, ts: now - 180, usage: { total_input_tokens: 68000, completion_tokens: 240, cache_read_input_tokens: 61000, cache_observable: true } },
  { id: "thought", kind: "thinking", content: "Compare the completed deployment with the existing skill before updating it.", ts: now - 164 },
  { id: "tool1", kind: "tool", name: "get_skill", status: "running", arguments: '{"name":"deployment-check"}', targets: [], request_id: "request-1", ts: now - 10 },
] };

test("background hook details show real execution and update without cluttering the feed", async ({ page }, info) => {
  let snapshot = { epoch: "hooks", revision: 1, runs: [parent, child] };
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: snapshot }));
  await page.goto("/webui/");
  const feed = page.getByRole("region", { name: "Global execution" });
  await expect(page.locator('.activity-feed [data-activity-run="hook-child"]')).toHaveCount(0);
  await expect(page.getByRole("button", { name: /skill_review Reply complete/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Background tasks", exact: true }).click();
  await page.getByRole("button", { name: /skill_review Reply complete/ }).click();
  await expect(page.getByText("Full transcript · 109 messages")).toBeVisible();
  await page.getByRole("button", { name: "View execution details", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "View execution details" });
  await expect(dialog.getByText("review-model", { exact: true })).toBeVisible();
  const model = dialog.getByRole("button", { name: /^review-model/ });
  await expect(model).toHaveAttribute("aria-expanded", "false");
  await model.click();
  await expect(dialog.getByText(/Compare the completed deployment/)).toBeVisible();
  const tool = dialog.getByRole("button", { name: "View details: get_skill", exact: true });
  await expect(tool).toContainText("Running");
  snapshot = { ...snapshot, revision: 2, runs: [parent, { ...child, revision: 2, status: "completed", ended_at: now + 1, entries: child.entries.map((entry) => entry.kind === "tool" ? { ...entry, status: "done", duration_ms: 10200, result: '{"name":"deployment-check","content":"Verify service health before restarting"}' } : entry) }] };
  await expect(tool).toContainText("Completed", { timeout: 8000 });
  await tool.click();
  const toolDetails = page.getByRole("dialog", { name: "get_skill", exact: true });
  await expect(toolDetails.getByRole("region", { name: "Result", exact: true })).toContainText("Verify service health before restarting");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("hook-details-light.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("hook-details-dark.png"), animations: "disabled" });
  await toolDetails.getByRole("button", { name: "Close", exact: true }).click();
  await expect(tool).toBeFocused();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(feed).toBeVisible();
  expect(errors).toEqual([]);
});

test("hooks page opens the same execution details and explains expired memory", async ({ page }) => {
  await page.route("**/api/hooks", (route) => route.fulfill({ json: { execution: { active: [hook], recent: [] } } }));
  let reads = 0;
  let fail = false;
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => {
    reads++;
    return route.fulfill(fail ? { status: 503, json: { detail: "Activity temporarily unavailable" } }
      : { json: { epoch: "new-process", revision: 0, runs: [] } });
  });
  await page.goto("/webui/hooks");
  await page.getByRole("button", { name: /skill_review Reply complete/ }).click();
  await page.getByRole("button", { name: "View execution details", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "View execution details" });
  await expect(dialog).toContainText("These execution details have left memory");
  const afterLoad = reads;
  await page.clock.install();
  await page.clock.runFor(6500);
  expect(reads).toBe(afterLoad);
  fail = true;
  await dialog.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(dialog).toContainText("Activity temporarily unavailable");
  await expect(dialog.getByText(/These execution details have left memory/)).toHaveCount(0);
  fail = false;
  await dialog.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(dialog).toContainText("These execution details have left memory");
});

test("Jev decisions remain visible after later work and expose slow responses", async ({ page }, info) => {
  const assessment = { id: "jev-1", provider: "Jev", task: "Deep memory recall", started_at: now - 5, threshold: 0.1,
    status: "skip" as const, duration_ms: 245, reason: "Existing facts fully cover the request", probability: 0.02,
    basis: "Already covered", basis_probability: 0.98, model: "jev-test", input_tokens: 600, output_tokens: 40,
    input_chars: 1500, input_truncated: false, slow_after_ms: 1000, slow: false };
  const review = { ...hook, name: "memory.probe", status: "done", phase: "done", operation: "Later retrieval completed", detail: "Subsequent work finished", judgments: [assessment,
    { ...assessment, id: "jev-2", task: "Skill review", status: "fallback" as const, probability: null, basis: "", basis_probability: null,
      input_tokens: 0, output_tokens: 0, duration_ms: 2001, slow: true, reason: "Request timed out; original work continues" }] };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: { epoch: "jev", revision: 1, runs: [
    { ...parent, entries: [{ ...review, ts: review.started_at, kind: "hook" }] },
  ] } }));
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/webui/");
  await expect(page.locator('[data-judgment]')).toHaveCount(0);
  await page.getByRole("button", { name: "Decisions", exact: true }).click();
  const judgment = page.locator('[data-judgment="jev-1"]');
  await expect(judgment.getByText("Skip this task", { exact: true })).toBeVisible();
  await expect(judgment.getByText("Need to execute 2%")).toHaveCount(0);
  expect((await judgment.boundingBox())!.height).toBeLessThanOrEqual(44);
  await expect(judgment.getByText("245ms", { exact: true })).toBeVisible();
  await expect(page.getByText("Subsequent work finished")).toHaveCount(0);
  const slow = page.locator('[data-judgment="jev-2"]');
  expect((await slow.boundingBox())!.height).toBeLessThanOrEqual(44);
  await expect(slow.getByLabel(/Slow response/)).toBeVisible();
  await expect(slow.getByRole("status")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("jev-compact.png"), animations: "disabled" });
  await judgment.getByRole("button").click();
  await expect(judgment.getByText("Need to execute 2%")).toBeVisible();
  await expect(judgment.getByText("Existing facts fully cover the request")).toBeVisible();
  await expect(judgment.getByText("Input 600 · output 40 tokens")).toBeVisible();
  await slow.getByRole("button").click();
  await expect(slow.getByRole("status")).toContainText("Slow response");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include('[data-judgment]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("jev-decisions.png"), animations: "disabled" });
  await slow.getByRole("link", { name: "Check channel speed" }).click();
  await expect(page).toHaveURL(/models\?tab=judgment/);
  expect(errors).toEqual([]);
});

test("Jev channel test reports a slow native request without hiding it", async ({ page }, info) => {
  await page.route("**/api/judgment/providers", (route) => route.fulfill({ json: {
    providers: { native: { name: "Decision service", base_url: "https://judge.test", api_key: "********",
      models: { test: { name: "Test decision", model: "jev-test" } } } },
    default_model: "test", fallback_models: [], revision: "1", health: {},
  } }));
  await page.route("**/api/judgment/status", (route) => route.fulfill({ json: {
    enabled: true, channel: "typesafe", api_key_configured: true, base_url: "https://judge.test", model: "jev-test",
    timeout: 30, fallback_enabled: true, fallback_model: "", fallback_effort: "low",
  } }));
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [] } }));
  await page.route("**/api/judgment/test", (route) => {
    expect(route.request().postDataJSON()).toEqual({ model_id: "test", inference: true });
    return route.fulfill({ json: {
      ok: true, kind: "inference", source: "typesafe", model: "jev-test", latency_ms: 2500, slow: true, slow_after_ms: 1000, gate_budget_ms: 2000,
      answers: { needed: { type: "noul", noul: 0.1 } }, missing: [], usage: { input_tokens: 80, output_tokens: 20 },
    } });
  });
  await page.goto("/webui/models?tab=judgment");
  await page.getByRole("article", { name: "Decision service" }).getByRole("button", { name: "Test", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Test inference (uses quota)" }).click();
  await expect(page.getByRole("status")).toContainText("Slow response: 2500ms");
  await expect(page.getByText(/Exceeds the 2000ms assessment budget/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("jev-speed.png"), animations: "disabled" });
});

test("LLM hook configuration reuses the shared metadata writer", async ({ page }) => {
  let context = "transcript";
  await page.route("**/api/hooks-llm", (route) => route.fulfill({ json: { enabled: true, runtime_started: true, events: ["after_reply"], governance: { max_concurrent: 2, transcript_enabled: true }, hooks: [{ name: "skill_review", event: "after_reply", context, mode: "async", source: "code", enabled: true, max_iterations: 6, max_concurrent: 1, cooldown_seconds: 120, stall_seconds: 180, route_output: false, debounce_seconds: 0, tool_tags: ["skills"], owner: "skills.review", config_group: "hooks_llm/skill_review" }] } }));
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "hooks_llm/skill_review", items: [{ key: "llm_hook_skill_review_context", description: "Context inheritance", type: "enum", value: context, default: "transcript", options: ["transcript", "lean", "none"], editable: true }] }] } }));
  await page.route("**/api/config/meta/llm_hook_skill_review_context", (route) => { context = route.request().postDataJSON().value; return route.fulfill({ json: { saved: true } }); });
  await page.goto("/webui/hooks?tab=llm");
  await expect(page.getByText("Notify AI after 180s without progress", { exact: true })).toBeVisible();
  await expect(page.getByText("Quiet on success; notify AI on failure", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Configure", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Configure" });
  await dialog.getByRole("combobox").selectOption("none");
  await expect.poll(() => context).toBe("none");
  await expect(dialog.getByRole("combobox")).toHaveValue("none");
});

test("memory enrichment reports the unfinished stage without fabricated call counts", async ({ page }, info) => {
  const probe: HookExecution = { ...hook, id: "probe", task_id: "", name: "memory.probe", event: "context_prepare", mode: "sync", source: "internal", status: "error", duration_ms: 5000, phase: "error", operation: "Cognee GRAPH_COMPLETION · 2 pending lanes", detail: "Retrieval budget exhausted. Completed results retained: 420 characters.", context_mode: "", activity_ids: [], model_calls: null, tool_calls: null };
  await page.route("**/api/hooks", (route) => route.fulfill({ json: { execution: { active: [], recent: [probe] } } }));
  await page.goto("/webui/hooks");
  await page.getByRole("button", { name: /Initial deep recall/ }).click();
  await expect(page.getByText("Model and tool calls are not counted for this internal process")).toBeVisible();
  await expect(page.getByText("0 model calls · 0 tool calls", { exact: true })).toHaveCount(0);
  await expect(page.getByText(probe.operation ?? "", { exact: true })).toBeVisible();
  await expect(page.getByText(probe.detail, { exact: true })).toBeVisible();
  await page.getByText(probe.detail, { exact: true }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("memory-probe-timeout.png"), animations: "disabled" });
});
