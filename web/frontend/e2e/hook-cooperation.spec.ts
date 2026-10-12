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
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: snapshot }));
  await page.goto("/webui/");
  const feed = page.getByRole("region", { name: "Global execution" });
  await expect(page.locator('.activity-feed [data-activity-run="hook-child"]')).toHaveCount(0);
  await page.getByRole("button", { name: /skill_review Reply complete/ }).click();
  await expect(page.getByText("Full transcript · 109 messages")).toBeVisible();
  await page.getByRole("button", { name: "View execution details", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "View execution details" });
  await expect(dialog.getByText("review-model", { exact: true })).toBeVisible();
  await expect(dialog.getByText(/Compare the completed deployment/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: /get_skill Running/ })).toBeVisible();
  snapshot = { ...snapshot, revision: 2, runs: [parent, { ...child, revision: 2, status: "completed", ended_at: now + 1, entries: child.entries.map((entry) => entry.kind === "tool" ? { ...entry, status: "done", duration_ms: 10200, result: '{"name":"deployment-check","content":"Verify service health before restarting"}' } : entry) }] };
  await expect(dialog.getByRole("button", { name: /get_skill Completed/ })).toBeVisible({ timeout: 8000 });
  await dialog.getByRole("button", { name: /get_skill Completed/ }).click();
  await expect(dialog.getByText("Verify service health before restarting", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("hook-details-light.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("hook-details-dark.png"), animations: "disabled" });
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(feed).toBeVisible();
  expect(errors).toEqual([]);
});

test("hooks page opens the same execution details and explains expired memory", async ({ page }) => {
  await page.route("**/api/hooks", (route) => route.fulfill({ json: { execution: { active: [hook], recent: [] } } }));
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "new-process", revision: 0, runs: [] } }));
  await page.goto("/webui/hooks");
  await page.getByRole("button", { name: /skill_review Reply complete/ }).click();
  await page.getByRole("button", { name: "View execution details", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("These execution details have left memory");
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
