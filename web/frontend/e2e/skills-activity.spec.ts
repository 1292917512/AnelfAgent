import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";
import type { SkillItem } from "../src/lib/types/workspace";

const skill: SkillItem = { name: "deployment-check", description: "Inspect a service before changing its configuration", content: "# Deployment checklist\n\n1. Read the service state.\n2. Check the expected version.\n3. Report verified facts.\n\n**Do not claim success without evidence.**", trigger_patterns: ["deployment", "service", "version"], state: "active", use_count: 12, match_count: 38, patch_count: 3, pinned: true, embedded: true, created_by: "agent", rationale: "Repeated deployment checks", merged_into: "", created_at: 1, last_activity_at: 1728800000, last_match_at: 1728800000 };
const ts = Date.now() / 1000;
const run: ActivityRun = {
  id: "skills-review", revision: 1, scope: "group_qq:42", origin_scope: "group_qq:42", source: { scope: "group_qq:42", kind: "group", channel: "qq", target: "42", session: "" },
  actor: "", label: "Inspect the home service and explain the available operations", kind: "conversation", input: "Inspect the home service", owner_id: "", parent_id: "", status: "completed", started_at: ts - 24, updated_at: ts, ended_at: ts, entry_count: 4, truncated: false,
  skills: [
    { name: skill.name, description: skill.description, mode: "read", score: 0.68, state: "active" },
    { name: "legacy-check", description: "Historical notes", mode: "candidate", score: 0.31, state: "archived" },
  ],
  entries: [
    { id: "ctx", kind: "context", status: "done", duration_ms: 1200, ts: ts - 24, blocks: [], block_count: 1, skills: [
      { name: skill.name, description: skill.description, mode: "candidate", score: 0.68, state: "active" },
      { name: "legacy-check", description: "Historical notes", mode: "candidate", score: 0.31, state: "archived" },
    ] },
    { id: "get", kind: "tool", name: "get_skill", status: "done", arguments: JSON.stringify({ name: skill.name }), targets: [], request_id: "request-1", result: JSON.stringify({ ok: true, ...skill }), duration_ms: 12, ts: ts - 20 },
    { id: "recall", kind: "tool", name: "recall", status: "done", arguments: JSON.stringify({ query: "service deployment status", filter_tags: "topic:deployment" }), targets: [], request_id: "request-2", result: JSON.stringify({ count: 2, results: [{ id: "mem:1", source: "memory", content: "The last deployment passed health checks.", tags: ["topic:deployment"], score: 0.87 }, { id: "mem:2", source: "memory", content: "Inspect the current version before restarting.", tags: ["topic:deployment", "channel:qq"], score: 0.72 }] }), duration_ms: 787, ts: ts - 16 },
    { id: "fail", kind: "tool", name: "get_skill", status: "error", arguments: '{"name":"missing-check"}', targets: [], request_id: "request-3", result: '{"error":"Skill not found"}', duration_ms: 5, ts: ts - 1 },
  ],
};

test("skill evidence distinguishes suggestions from reads and links to a readable catalog", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "skills-test", revision: 1, runs: [run] } }));
  await page.route("**/api/skills/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/health")) return route.fulfill({ json: { counts: { active: 1, stale: 0, archived: 1, pinned: 1 }, capacity_reference: 100, zero_engagement: [], high_match_low_use: [], trigger_collisions: {}, parse_errors: {} } });
    if (path.endsWith(`/${skill.name}`)) return route.fulfill({ json: skill });
    if (path.endsWith("/legacy-check")) return route.fulfill({ json: { ...skill, name: "legacy-check", state: "archived" } });
    return route.fulfill({ json: [skill, { ...skill, name: "legacy-check", state: "archived", pinned: false }] });
  });
  await page.goto("/webui/");
  const execution = page.locator('[data-activity-run="skills-review"]');
  const evidence = execution.locator(".activity-run-content > .skill-evidence");
  await expect(evidence.getByRole("link", { name: /deployment-check Content read Match 0.68/ })).toBeVisible();
  await expect(evidence.getByRole("link", { name: /legacy-check Suggested Match 0.31 Archived/ })).toBeVisible();
  await expect(evidence.getByText("missing-check")).toHaveCount(0);
  await expect(execution.locator(".activity-tool-summary").filter({ hasText: "service deployment status" })).toContainText("Required tags: topic:deployment");
  await expect(execution.locator(".activity-tool-summary").filter({ hasText: "service deployment status" })).toContainText("2 results");
  await expect(execution.getByRole("button", { name: /recall Completed/ })).toHaveAttribute("aria-expanded", "true");
  await expect(execution.locator(".activity-recall-results")).toContainText("The last deployment passed health checks.");
  await expect(execution.locator(".activity-recall-tags").first()).toContainText("topic:deployment");
  await execution.getByRole("button", { name: /recall Completed/ }).click();
  await execution.getByRole("button", { name: /get_skill Failed/ }).click();
  await page.locator(".activity-feed").evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: info.outputPath("skills-workspace-light.png"), animations: "disabled" });
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("skills-workspace-dark.png"), animations: "disabled" });
  await evidence.getByRole("link", { name: /deployment-check Content read/ }).click();
  await expect(page).toHaveURL(/skills\?skill=deployment-check/);
  const card = page.locator('[data-skill="deployment-check"]');
  await expect(card.getByRole("heading", { name: "Deployment checklist" })).toBeVisible();
  await expect(card.locator(".skill-card-facts")).toContainText("38");
  await expect(page.getByRole("region", { name: "Recent skill activity" })).toContainText("qq · 42");
  const populatedAudit = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(populatedAudit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("skills-catalog-dark.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Archived · 1", exact: true }).click();
  await expect(page.locator('[data-skill="deployment-check"]')).toHaveCount(0);
  await expect(page.locator('[data-skill="legacy-check"]')).toBeVisible();
  await page.getByRole("textbox", { name: "Search skills..." }).fill("does-not-exist");
  await expect(page.getByText("No skills match these filters")).toBeVisible();
  await page.getByRole("region", { name: "Recent skill activity" }).getByRole("link", { name: /deployment-check Content read/ }).click();
  await expect(page.getByRole("textbox", { name: "Search skills..." })).toHaveValue("");
  await expect(page.getByRole("tab", { name: "All · 2", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(card.getByRole("heading", { name: "Deployment checklist" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  expect(errors).toEqual([]);
});
