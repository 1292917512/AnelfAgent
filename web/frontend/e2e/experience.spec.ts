import { test, expect } from "./fixtures";

test("history outages remain retryable without presenting a false empty conversation", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/chat/history*", (route) => {
    reads++;
    return reads === 1 ? route.fulfill({ status: 503, json: { detail: "History temporarily unavailable" } })
      : route.fulfill({ json: [{ id: 1, role: "assistant", content: "Recovered history", ts: 1 }] });
  });
  await page.goto("/webui/");
  await expect(page.getByRole("alert")).toContainText("History temporarily unavailable");
  await page.getByRole("alert").getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Recovered history", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).not.toBeVisible();
});

test("late initial history never replaces a newly submitted message", async ({ page }) => {
  let release!: () => void;
  const ready = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/chat/history*", async (route) => {
    await ready;
    await route.fulfill({ json: [{ id: 1, role: "assistant", content: "Earlier context", ts: 1 }] });
  });
  await page.route("**/api/chat/send", (route) => route.fulfill({ json: { ok: true } }));
  await page.goto("/webui/");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("New message during loading");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
  release();
  await expect(page.getByText("Earlier context", { exact: true })).toBeVisible();
  await expect(page.getByText("New message during loading", { exact: true })).toBeVisible();
});

test("file conflicts show both versions and use the latest revision only after explicit replacement", async ({ page }) => {
  let version = "v1";
  let content = "Original file";
  let writes = 0;
  await page.route("**/api/workspace/file*", async (route) => {
    if (route.request().method() === "PUT") {
      writes++;
      const body = route.request().postDataJSON() as { content: string; expected_version: string };
      if (writes === 1) {
        version = "external-v2"; content = "External edit";
        await route.fulfill({ status: 409, json: { detail: "Changed on disk" } }); return;
      }
      expect(body.expected_version).toBe("external-v2");
      content = body.content; version = "v3";
    }
    await route.fulfill({ json: { path: "note.txt", name: "note.txt", version, content, size: content.length, modified: 0, binary: false, truncated: false } });
  });
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("Original file");
  await page.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText("Local draft");
  await expect(page.locator(".cm-content")).toHaveText("Local draft");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "File changed on disk" });
  await expect(dialog.getByText("Local draft", { exact: true }).last()).toBeVisible();
  await expect(dialog.getByText("External edit", { exact: true })).toBeVisible();
  expect(writes).toBe(1);
  await dialog.getByRole("button", { name: "Replace with local draft", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(writes).toBe(2);
  await expect(page.locator(".cm-content")).toHaveText("Local draft");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
});

test("skills protect editor drafts and keep failed saves visible", async ({ page }) => {
  const skill = { name: "research", description: "Research carefully", content: "Original instructions", trigger_patterns: ["research"],
    state: "active", use_count: 4, match_count: 7, patch_count: 1, pinned: false, embedded: null, created_by: "user", rationale: "", merged_into: "" };
  await page.route("**/api/skills/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/research") && route.request().method() === "PUT") return route.fulfill({ status: 503, json: { detail: "Skill storage unavailable" } });
    if (path.endsWith("/research")) return route.fulfill({ json: skill });
    if (path.endsWith("/health")) return route.fulfill({ json: { counts: { active: 1, stale: 0, archived: 0, pinned: 0 }, capacity_reference: 100,
      zero_engagement: [], high_match_low_use: [], trigger_collisions: {}, parse_errors: {} } });
    return route.fulfill({ json: [skill] });
  });
  await page.goto("/webui/skills");
  await page.getByRole("button", { name: /research Research carefully/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Edit · research" });
  await dialog.getByRole("textbox", { name: "Content (Markdown)", exact: true }).fill("Unsaved instructions");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Content (Markdown)", exact: true })).toHaveValue("Unsaved instructions");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Skill storage unavailable");
  await expect(dialog.getByRole("textbox", { name: "Content (Markdown)", exact: true })).toHaveValue("Unsaved instructions");
});

test("workflow completion fetches final output and revision brings back the full specification", async ({ page }) => {
  let finished = false;
  let detailReads = 0;
  const spec = { name: "Research report", steps: [{ key: "research", kind: "ask", goal: "Research topic" }, { key: "summary", kind: "ask", goal: "Summarize", depends_on: ["research"] }] };
  const run = () => ({ run_id: "run-a", name: spec.name, status: finished ? "completed" : "running", running: !finished, created_at: 1700000000 });
  await page.route("**/api/workflow/runs*", (route) => route.fulfill({ json: { runs: [run()] } }));
  await page.route("**/api/workflow/runs/run-a", async (route) => {
    detailReads++;
    await route.fulfill({ json: { run: run(), spec, events: [], events_truncated: false,
      nodes: finished ? spec.steps.map((step) => ({ key: step.key, kind: "ask", ordinal: 1, status: "completed", result: step.key === "summary" ? "Full final report" : "Research findings", error: "", usage: null, delegation_id: "", created_at: 1, updated_at: 2 })) : [] } });
  });
  await page.goto("/webui/workflow?run=run-a");
  await expect(page.getByText("Waiting for dependencies", { exact: true })).toHaveCount(2);
  finished = true;
  await expect(page.getByText("2 / 2 steps completed", { exact: true })).toBeVisible({ timeout: 12000 });
  expect(detailReads).toBeGreaterThan(1);
  await page.locator("summary").filter({ hasText: "summary" }).first().click();
  await expect(page.getByText("Full final report", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Revise & restart", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Workflow specification", exact: true })).toHaveValue(JSON.stringify(spec, null, 2));
});

test("message context can be inspected and excluded without hiding the submitted message", async ({ page }) => {
  await page.route("**/api/chat/send", (route) => route.fulfill({ json: { ok: true } }));
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("workspace content");
  await page.getByRole("button", { name: "Collapse panel (tabs kept)", exact: true }).click();
  const context = page.getByRole("checkbox", { name: "Include workspace context", exact: true });
  await expect(context).toBeChecked();
  await context.uncheck();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Check this");
  const request = page.waitForRequest((req) => req.method() === "POST" && req.url().endsWith("/chat/send"));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  expect((await request).postDataJSON().workspace_context).toBeUndefined();
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Copy to draft (original kept)", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Check this");
  await expect(page.getByText("Submitted", { exact: true })).toBeVisible();
});
