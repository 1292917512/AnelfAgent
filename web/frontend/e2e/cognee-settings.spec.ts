import { expect } from "@playwright/test";
import { test } from "./fixtures";
import type { CogneeSettingField } from "../src/lib/types";

test("Cognee cleanup progress advances without claiming the index is already committed", async ({ page }, info) => {
  let completed = 186;
  const total = 385;
  await page.route("**/api/memory/cognee/status", route => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready: true, version: "1.6.5" },
    sync: { enabled: true, running: true, pending: 25, failed: 0, synced: 4406, processing: 25,
      active: { stage: "replace_projection", items: 25, attempt: 2, dataset_name: "relations", started_at: 1000,
        elapsed_seconds: 1800, stage_elapsed_seconds: 600, quiet_seconds: 1, last_change_at: 1400,
        timeout_seconds: 0, pipeline: null, progress_checked_at: 0, progress_error: "",
        cleanup: { total, completed, last_completed_at: 1400 + completed } } },
  } }));
  await page.goto("/webui/memory?tab=cognee");
  const progress = page.getByTestId("cognee-batch-progress");
  await expect(progress).toContainText("186 / 385");
  await expect(progress.getByRole("progressbar")).toHaveAttribute("value", "186");
  await expect(progress.getByRole("status")).toHaveCount(0);
  expect(await progress.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  completed = 220;
  await expect(progress).toContainText("220 / 385", { timeout: 10000 });
  await progress.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("cognee-cleanup-progress.png"), animations: "disabled" });
  completed = total;
  await expect(progress.getByRole("status")).toContainText("awaiting index validation and a safe commit", { timeout: 10000 });
});

test("Cognee maintenance is hidden, explained, confirmed and tracked without holding the page", async ({ page }, info) => {
  let maintenance: { mode: "cleanup" | "compact"; phase: "queued" | "running" } | null = null;
  let summary = "";
  const requests: boolean[] = [];
  await page.route("**/api/memory/cognee/status", route => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready: true },
    sync: { enabled: false, running: false, pending: 10, failed: 0, synced: 30,
      maintenance, last_compact_summary: summary },
  } }));
  await page.route("**/api/memory/cognee/compact?*", route => {
    const prune = new URL(route.request().url()).searchParams.get("prune_only") === "true";
    requests.push(prune);
    maintenance = { mode: prune ? "cleanup" : "compact", phase: "queued" };
    return route.fulfill({ json: { ok: true, scheduled: true } });
  });
  await page.goto("/webui/memory?tab=cognee");
  await expect(page.getByRole("button", { name: "Clean expired versions", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Start a fresh full rebuild", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Index maintenance", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Index maintenance", exact: true });
  const cleanup = panel.getByRole("button", { name: "Clean expired versions", exact: true });
  await expect(cleanup).toHaveAttribute("title", /without merging fragments/);
  await expect(cleanup).toHaveAccessibleDescription(/Keep current memories/);
  expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("cognee-maintenance.png"), animations: "disabled" });
  await cleanup.click();
  const confirm = page.getByRole("dialog", { name: "Clean expired versions", exact: true });
  await expect(confirm).toContainText("Current memories and native data are retained");
  expect(requests).toEqual([]);
  await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(confirm).toBeHidden();
  await expect(cleanup).toBeFocused();
  expect(requests).toEqual([]);
  await cleanup.click();
  await confirm.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(() => requests).toEqual([true]);
  await expect(panel.getByRole("status")).toContainText("waiting for the current batch");
  await expect(cleanup).toBeDisabled();
  await panel.getByRole("button", { name: "Close", exact: true }).click();
  await expect(panel).toBeHidden();
  await expect(page.getByRole("status").filter({ hasText: "Clean expired versions:" })).toContainText("waiting");
  maintenance = { mode: "cleanup", phase: "running" };
  await expect(page.getByRole("status").filter({ hasText: "Clean expired versions:" })).toContainText("running", { timeout: 10000 });
  maintenance = null;
  summary = "Maintenance completed; current data retained";
  await expect(page.getByText(/Maintenance completed; current data retained/)).toBeVisible({ timeout: 10000 });
  await page.getByRole("button", { name: "Index maintenance", exact: true }).click();
  const compact = panel.getByRole("button", { name: "Compact Storage", exact: true });
  await expect(compact).toHaveAttribute("title", /temporary space/);
  await compact.click();
  const compactConfirm = page.getByRole("dialog", { name: "Compact Storage", exact: true });
  await compactConfirm.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(() => requests).toEqual([true, false]);
});

test("Cognee settings separate deadlines and preserve overrides across edits and tabs", async ({ page }, info) => {
  const fields: CogneeSettingField[] = [
    { key: "enabled", type: "bool", section: "models" },
    { key: "timeout_seconds", type: "float", section: "recall", min: 1 },
    { key: "search_types", type: "string_list", section: "recall", advanced: true },
    { key: "pipeline_timeout_seconds", type: "float", section: "sync", min: 1 },
    { key: "sync_batch_size", type: "int", section: "sync", min: 1 },
    { key: "native_watchdog_enabled", type: "bool", section: "storage", advanced: true },
    { key: "compact_cleanup_interval_seconds", type: "float", section: "storage", advanced: true, min: 60 },
    { key: "data_root", type: "string", section: "storage", read_only: true, advanced: true },
  ];
  let config = {
    enabled: true, timeout_seconds: 300, pipeline_timeout_seconds: 3600, sync_batch_size: 2,
    native_watchdog_enabled: true, data_root: "/data/memory/cognee", search_types: ["CHUNKS"],
    compact_cleanup_interval_seconds: 600,
    chat: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "",
      api_version: "", instructor_mode: "", max_completion_tokens: 0, reasoning_effort: "",
      extra_args: { top_p: 0.9 } as Record<string, unknown> },
    embedding: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", dimensions: 0 },
    options: { chat_providers: ["openai"], embed_providers: ["openai"], instructor_modes: ["", "tools", "json_mode"],
      reasoning_efforts: ["", "low", "high"], settings_fields: fields },
  };
  const patches: Record<string, unknown>[] = [];
  let rejectNextSave = false;
  await page.route("**/api/memory/cognee/config", async (route) => {
    if (route.request().method() === "PUT") {
      const patch = route.request().postDataJSON();
      patches.push(patch);
      if (rejectNextSave) {
        rejectNextSave = false;
        return route.fulfill({ status: 400, json: { detail: "Embedding compatibility check rejected this change" } });
      }
      config = { ...config, ...patch };
    }
    await route.fulfill({ json: config });
  });
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready: true, version: "1.6.5" },
    projection: null, sync: { running: false, pending: 0, failed: 0, synced: 3, configuration_pending: true },
    resolved: { chat: { model: "test-model", request_timeout_seconds: 120 }, embedding: {} },
  } }));
  await page.goto("/webui/memory?tab=cognee");
  await page.getByRole("button", { name: "Models and configuration", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Models and configuration" });
  const models = dialog.getByRole("tabpanel", { name: "Extraction models", exact: true });
  const timeout = models.getByRole("spinbutton", { name: "Model request timeout (seconds)" });
  await expect(dialog).toHaveCSS("opacity", "1");
  await page.screenshot({ path: info.outputPath("cognee-settings-overview.png") });
  await expect(timeout).toHaveValue("");
  await expect(models.getByText("Active request timeout: 120 seconds")).toBeVisible();
  await timeout.fill("0");
  await expect(models.getByRole("alert")).toContainText("greater than 0");
  await expect(models.getByRole("button", { name: "Save", exact: true }).first()).toBeDisabled();
  await timeout.fill("300");
  await models.getByRole("button", { name: "Custom", exact: true }).first().click();
  await models.getByRole("button", { name: "Auto", exact: true }).first().click();
  expect(patches).toHaveLength(0);
  await dialog.getByRole("tab", { name: "Recall queries" }).click();
  await expect(dialog.getByRole("spinbutton", { name: "Query call timeout (seconds)" })).toHaveValue("300");
  await dialog.getByRole("tab", { name: "Extraction models", exact: true }).click();
  await expect(timeout).toHaveValue("300");
  await models.getByRole("button", { name: "Save", exact: true }).first().click();
  await expect.poll(() => patches.length).toBe(1);
  expect(config.chat.extra_args).toEqual({ top_p: 0.9, timeout: 300 });
  expect(config.timeout_seconds).toBe(300);
  expect(config.pipeline_timeout_seconds).toBe(3600);
  await expect(models.getByRole("button", { name: "Saved", exact: true }).first()).toBeVisible();
  await page.screenshot({ path: info.outputPath("cognee-model-timeout.png") });
  await timeout.fill("");
  await models.getByRole("button", { name: "Save", exact: true }).first().click();
  await expect.poll(() => patches.length).toBe(2);
  expect(config.chat.extra_args).toEqual({ top_p: 0.9 });
  await dialog.getByRole("tab", { name: "Sync and build" }).click();
  await expect(dialog.getByRole("spinbutton", { name: "Build stage timeout (seconds)" })).toHaveValue("3600");
  await dialog.getByRole("spinbutton", { name: "Build stage timeout (seconds)" }).fill("4200");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(3);
  expect(patches[2]).toEqual({ pipeline_timeout_seconds: 4200 });
  await dialog.getByRole("tab", { name: "Storage protection" }).click();
  const storage = dialog.getByRole("tabpanel", { name: "Storage protection" });
  await storage.locator("summary").filter({ hasText: "Advanced settings" }).click();
  await expect(storage.getByRole("spinbutton", { name: "Version Cleanup Interval (s)" })).toHaveValue("600");
  await storage.getByRole("spinbutton", { name: "Version Cleanup Interval (s)" }).fill("120");
  await storage.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(4);
  expect(patches[3]).toEqual({ compact_cleanup_interval_seconds: 120 });
  await expect(storage.getByText("/data/memory/cognee", { exact: true })).toBeVisible();
  await expect(storage.getByRole("textbox")).toHaveCount(0);
  await expect(dialog.getByRole("link", { name: "View storage location in unified data management" })).toHaveAttribute("href", "/webui/data");
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("cognee-storage-settings.png") });

  await dialog.getByRole("tab", { name: "Extraction models", exact: true }).click();
  const unlock = models.getByRole("switch", { name: "Allow embedding model edits", exact: true });
  const unlockDialog = page.getByRole("dialog", { name: "Unlock embedding model settings?", exact: true });
  await expect(unlock).not.toBeChecked();
  await expect(models.getByRole("button", { name: "Pinned model", exact: true }).nth(1)).toBeDisabled();
  await unlock.click();
  await expect(unlockDialog).toContainText("full rebuild and verification");
  await unlockDialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(unlock).not.toBeChecked();
  expect(patches).toHaveLength(4);
  await unlock.click();
  await unlockDialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(unlock).toBeChecked();
  await models.getByRole("button", { name: "Pinned model", exact: true }).first().click();
  await models.getByRole("button", { name: "Pinned model", exact: true }).nth(1).click();
  const cards = models.locator("section").filter({ has: page.getByRole("button", { name: "Auto", exact: true }) });
  await expect(cards).toHaveCount(2);
  if (info.project.name === "desktop") {
    const boxes = await cards.evaluateAll(elements => elements.map(element => element.getBoundingClientRect().toJSON()));
    expect(Math.abs(boxes[0].height - boxes[1].height)).toBeLessThan(2);
    expect(boxes[0].y).toBe(boxes[1].y);
    const picker = await cards.first().getByRole("button", { name: "Select model", exact: true }).last().boundingBox();
    const reasoning = await models.getByRole("combobox", { name: "Reasoning effort", exact: true }).boundingBox();
    expect(picker).not.toBeNull();
    expect(reasoning).not.toBeNull();
    expect(Math.abs(picker!.y - reasoning!.y)).toBeLessThan(2);
  }
  expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await models.getByRole("button", { name: "Help: Model request timeout (seconds)", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "Model request timeout (seconds)", exact: true })).toContainText("Leave blank to inherit");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  await page.screenshot({ path: info.outputPath("cognee-model-layout.png"), animations: "disabled" });
  await unlock.click();
  await expect(unlock).not.toBeChecked();
  expect(patches).toHaveLength(4);
  expect(config.embedding.source).toBe("auto");

  const toggle = models.getByRole("switch", { name: "Enable Cognee", exact: true });
  await expect(toggle).toBeChecked();
  await toggle.click();
  const disable = page.getByRole("dialog", { name: "Disable “Enable Cognee”?", exact: true });
  await expect(disable).toContainText("Click Save to apply");
  await disable.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(toggle).toBeChecked();
  expect(patches).toHaveLength(4);
  await toggle.click();
  await disable.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(toggle).not.toBeChecked();
  expect(patches).toHaveLength(4);
  await models.locator("section").filter({ has: page.getByRole("switch", { name: "Enable Cognee", exact: true }) }).getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(5);
  expect(patches[4]).toEqual({ enabled: false });
  await toggle.click();
  const enable = page.getByRole("dialog", { name: "Enable “Enable Cognee”?", exact: true });
  await enable.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(toggle).toBeChecked();
  expect(patches).toHaveLength(5);
  await models.locator("section").filter({ has: page.getByRole("switch", { name: "Enable Cognee", exact: true }) }).getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(6);
  expect(patches[5]).toEqual({ enabled: true });
  await toggle.click();
  await disable.getByRole("button", { name: "Confirm", exact: true }).click();
  await unlock.click();
  await unlockDialog.getByRole("button", { name: "Confirm", exact: true }).click();
  const embedding = cards.nth(1);
  await embedding.getByRole("button", { name: "Custom", exact: true }).click();
  await embedding.getByRole("textbox", { name: "Model identifier", exact: true }).fill("openai/new-embedding");
  await expect(embedding.getByRole("button", { name: "Save", exact: true })).toHaveCount(1);
  rejectNextSave = true;
  await embedding.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(7);
  await expect(embedding.getByRole("alert")).toContainText("Embedding compatibility check rejected");
  await expect(unlock).toBeChecked();
  await expect(toggle).not.toBeChecked();
  expect(config.enabled).toBe(true);
  expect(config.embedding.model).toBe("");
  await expect(embedding.getByRole("textbox", { name: "Model identifier", exact: true })).toHaveValue("openai/new-embedding");
  await embedding.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => patches.length).toBe(8);
  expect(patches[7]).toEqual({ enabled: false, embedding: config.embedding });
  expect(config.embedding.model).toBe("openai/new-embedding");
  await expect(unlock).not.toBeChecked();
  await expect(embedding.getByRole("textbox", { name: "Model identifier", exact: true })).toBeDisabled();
  await unlock.click();
  await unlockDialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Models and configuration", exact: true }).click();
  await expect(unlock).not.toBeChecked();
  if (info.project.name === "desktop") {
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page.getByRole("button", { name: "Appearance & language", exact: true }).click();
    await page.getByRole("button", { name: "Switch to dark theme", exact: true }).click();
    await page.getByRole("button", { name: "简体中文", exact: true }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "模型与配置", exact: true }).click();
    const chineseDialog = page.getByRole("dialog", { name: "模型与配置", exact: true });
    await chineseDialog.getByRole("button", { name: "指定模型", exact: true }).first().click();
    await expect(chineseDialog.getByRole("button", { name: "指定模型", exact: true }).nth(1)).toBeDisabled();
    await expect(chineseDialog).toHaveCSS("opacity", "1");
    await page.screenshot({ path: info.outputPath("cognee-model-layout-zh.png"), animations: "disabled" });
  }
});
