import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";
import type { SkillItem } from "../src/lib/types/workspace";

const skill: SkillItem = { name: "deployment-check", description: "Inspect a service before changing its configuration", content: "# Deployment checklist\n\n1. Read the service state.\n2. Check the expected version.\n3. Report verified facts.\n\n**Do not claim success without evidence.**", trigger_patterns: ["deployment", "service", "version"], state: "active", use_count: 12, match_count: 38, mcp_servers: [], patch_count: 3, pinned: true, embedded: true, created_by: "agent", rationale: "Repeated deployment checks", merged_into: "", created_at: 1, last_activity_at: 1728800000, last_match_at: 1728800000 };
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
    { id: "get", kind: "tool", name: "get_skill", status: "done", arguments: JSON.stringify({ name: skill.name }), targets: [{ key: "file_path", value: "skills/deployment-check/SKILL.md", root: "workspace", path: "skills/deployment-check/SKILL.md" }], request_id: "request-1", result: JSON.stringify({ ok: true, ...skill }), duration_ms: 12, ts: ts - 20 },
    { id: "recall", kind: "tool", name: "recall", status: "done", arguments: JSON.stringify({ query: "service deployment status", filter_tags: "topic:deployment" }), targets: [{ key: "file_path", value: "memory/deployment.md", root: "project", path: "config/memory/deployment.md" }], request_id: "request-2", result: JSON.stringify({ count: 2, results: [{ id: "mem:1", source: "memory", content: "The last deployment passed health checks.", tags: ["topic:deployment"], score: 0.87 }, { id: "mem:2", source: "memory", content: "Inspect the current version before restarting.", tags: ["topic:deployment", "channel:qq"], score: 0.72 }] }), duration_ms: 787, ts: ts - 16 },
    { id: "fail", kind: "tool", name: "get_skill", status: "error", arguments: '{"name":"missing-check"}', targets: [], request_id: "request-3", result: '{"error":"Skill not found"}', duration_ms: 5, ts: ts - 1 },
  ],
};

test("skill evidence distinguishes suggestions from reads and links to a readable catalog", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: { epoch: "skills-test", revision: 1, runs: [run] } }));
  await page.route("**/api/skills/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/health")) return route.fulfill({ json: { counts: { active: 1, stale: 0, archived: 1, pinned: 1 }, capacity_reference: 100, zero_engagement: [], high_match_low_use: [], trigger_collisions: {}, parse_errors: {} } });
    if (path.endsWith(`/${skill.name}`)) return route.fulfill({ json: skill });
    if (path.endsWith("/legacy-check")) return route.fulfill({ json: { ...skill, name: "legacy-check", state: "archived" } });
    return route.fulfill({ json: [skill, { ...skill, name: "legacy-check", state: "archived", pinned: false }] });
  });
  await page.goto("/webui/");
  const execution = page.locator('[data-activity-run="skills-review"]');
  const summary = execution.getByRole("button", { name: "Skills in this run", exact: true });
  await expect(summary).toContainText("Content read 1");
  await expect(summary).toContainText("Suggested 1");
  await expect(summary.getByText("missing-check")).toHaveCount(0);
  await expect(execution.locator('.file-reference[data-kind="skill"]')).toContainText("Skill");
  await expect(execution.locator('.file-reference[data-kind="memory"]')).toContainText("Memory");
  await expect(execution.locator(".activity-recall-results")).toHaveCount(0);
  await summary.click();
  const evidence = page.getByRole("dialog", { name: "Skills in this run", exact: true });
  await expect(evidence.getByRole("link", { name: /deployment-check.*Content read Match 0.68/ })).toBeVisible();
  await expect(evidence.getByRole("link", { name: /legacy-check.*Suggested Match 0.31 Archived/ })).toBeVisible();
  await expect(evidence).toContainText("does not confirm that their steps were completed");
  await evidence.getByRole("button", { name: "Close", exact: true }).click();
  await expect(execution.locator(".activity-tool-summary").filter({ hasText: "service deployment status" })).toContainText("Required tags");
  await expect(execution.locator('.activity-tool-summary [title="topic: deployment"]')).toBeVisible();
  await expect(execution.locator(".activity-tool-summary").filter({ hasText: "service deployment status" })).toContainText("2 results");
  await execution.getByRole("button", { name: "View details: recall", exact: true }).click();
  const recall = page.getByRole("dialog", { name: "recall", exact: true });
  await expect(recall.locator(".activity-recall-results")).toContainText("The last deployment passed health checks.");
  await expect(recall.locator('.activity-memory-tags [title="topic: deployment"]').first()).toBeVisible();
  await recall.getByRole("button", { name: "Close", exact: true }).click();
  await expect(execution.locator(".activity-recall-results")).toHaveCount(0);
  await page.locator(".activity-feed").evaluate((element) => { element.scrollTop = 0; });
  await page.screenshot({ path: info.outputPath("skills-workspace-light.png"), animations: "disabled" });
  const workspaceAudit = await new AxeBuilder({ page }).include(".activity-feed").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(workspaceAudit.violations).toEqual([]);
  await page.evaluate(() => { document.documentElement.dataset.theme = "dark"; document.documentElement.classList.add("dark"); });
  await page.screenshot({ path: info.outputPath("skills-workspace-dark.png"), animations: "disabled" });
  await summary.click();
  await evidence.getByRole("link", { name: /deployment-check.*Content read/ }).click();
  await expect(evidence).toHaveCount(0);
  await expect(page).toHaveURL(/skills\?skill=deployment-check/);
  const card = page.locator('[data-skill="deployment-check"]');
  await expect(card.getByRole("heading", { name: "Deployment checklist" })).toBeVisible();
  await expect(card.locator(".skill-card-facts")).toContainText("38");
  await expect(page.getByRole("region", { name: "Recent skill activity" })).toContainText("qq · 42");
  await page.getByRole("region", { name: "Recent skill activity" }).screenshot({ path: info.outputPath("recent-skills-dark.png"), animations: "disabled" });
  const populatedAudit = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(populatedAudit.violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("skills-catalog-dark.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Archived · 1", exact: true }).click();
  await expect(page.locator('[data-skill="deployment-check"]')).toHaveCount(0);
  await expect(page.locator('[data-skill="legacy-check"]')).toBeVisible();
  await page.getByRole("textbox", { name: "Search skills..." }).fill("does-not-exist");
  await expect(page.getByText("No skills match these filters")).toBeVisible();
  const recent = page.getByRole("region", { name: "Recent skill activity" });
  await expect(recent.locator("li")).toHaveCount(2);
  await expect(recent.locator("li").first()).toContainText("deployment-check");
  await recent.getByRole("link", { name: "deployment-check", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Search skills..." })).toHaveValue("");
  await expect(page.getByRole("tab", { name: "All · 2", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(card.getByRole("heading", { name: "Deployment checklist" })).toBeVisible();
  await recent.getByRole("button", { name: "Edit deployment-check", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "Edit · deployment-check", exact: true });
  await expect(editor.locator("textarea")).toHaveValue(skill.content!);
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(editor).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  expect(errors).toEqual([]);
});


test("skill MCP links are editable locally and execution shows compact status snapshots", async ({ page }, info) => {
  let selected = ["docs"];
  let fail = true;
  const available = [
    { name: "docs", configured: true, enabled: true, connected: true, tool_count: 4, activation: "on_demand" },
    { name: "maps", configured: true, enabled: false, connected: false, tool_count: 0, activation: "unavailable" },
  ];
  await page.route("**/api/skills/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/mcp")) {
      if (route.request().method() === "PUT") {
        if (fail) { await route.fulfill({ status: 503, json: { detail: "Try again" } }); return; }
        selected = route.request().postDataJSON().servers as string[];
        await route.fulfill({ json: { name: skill.name, mcp_servers: selected } }); return;
      }
      await route.fulfill({ json: { name: skill.name, servers: available.filter((item) => selected.includes(item.name)), available } }); return;
    }
    if (path.endsWith(`/${skill.name}`)) { await route.fulfill({ json: { ...skill, mcp_servers: selected } }); return; }
    if (path.endsWith("/health")) { await route.fulfill({ status: 503, json: { detail: "Unavailable" } }); return; }
    await route.fulfill({ json: [{ ...skill, mcp_servers: selected }] });
  });
  const states = [{ ...available[0], activation: "sleeping" }];
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: { epoch: "links", revision: 1, runs: [{ ...run,
    skills: [{ name: skill.name, description: skill.description, mode: "read", mcp_status: states }],
    entries: [{ ...run.entries[1], result: JSON.stringify({ ok: true, name: skill.name, content: "Steps", mcp_status: states }) }],
  }] } }));
  await page.goto(`/webui/skills?skill=${skill.name}`);
  await page.getByRole("button", { name: "MCP links · 1", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `MCP links · ${skill.name}`, exact: true });
  await expect(dialog.getByText("Activate per conversation", { exact: true })).toBeVisible();
  await dialog.getByRole("checkbox", { name: /maps/ }).check();
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByText("Try again", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: /maps/ })).toBeChecked();
  await page.screenshot({ path: info.outputPath("skill-mcp-editor.png") });
  fail = false;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("button", { name: "MCP links · 2", exact: true })).toBeVisible();
  await page.goto("/webui/");
  const badge = page.getByRole("button", { name: "MCP 1 · 0 active", exact: true }).first();
  await expect(badge).toBeVisible();
  await badge.click();
  const status = page.getByRole("dialog", { name: "Linked MCP status", exact: true });
  await expect(status.getByText("Not activated", { exact: true })).toBeVisible();
  await expect(status.getByText("docs", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("skill-mcp-snapshot.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
