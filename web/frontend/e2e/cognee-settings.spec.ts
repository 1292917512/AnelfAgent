import { expect } from "@playwright/test";
import { test } from "./fixtures";
import type { CogneeSettingField } from "../src/lib/types";

test("Cognee settings separate deadlines and preserve overrides across edits and tabs", async ({ page }, info) => {
  const fields: CogneeSettingField[] = [
    { key: "enabled", type: "bool", section: "models" },
    { key: "timeout_seconds", type: "float", section: "recall", min: 1 },
    { key: "search_types", type: "string_list", section: "recall", advanced: true },
    { key: "pipeline_timeout_seconds", type: "float", section: "sync", min: 1 },
    { key: "sync_batch_size", type: "int", section: "sync", min: 1 },
    { key: "native_watchdog_enabled", type: "bool", section: "storage", advanced: true },
    { key: "data_root", type: "string", section: "storage", read_only: true, advanced: true },
  ];
  let config = {
    enabled: true, timeout_seconds: 300, pipeline_timeout_seconds: 3600, sync_batch_size: 2,
    native_watchdog_enabled: true, data_root: "/data/memory/cognee", search_types: ["CHUNKS"],
    chat: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "",
      api_version: "", instructor_mode: "", max_completion_tokens: 0, reasoning_effort: "",
      extra_args: { top_p: 0.9 } as Record<string, unknown> },
    embedding: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", dimensions: 0 },
    options: { chat_providers: ["openai"], embed_providers: ["openai"], instructor_modes: ["", "tools", "json_mode"],
      reasoning_efforts: ["", "low", "high"], settings_fields: fields },
  };
  const patches: Record<string, unknown>[] = [];
  await page.route("**/api/memory/cognee/config", async (route) => {
    if (route.request().method() === "PUT") {
      const patch = route.request().postDataJSON();
      patches.push(patch);
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
  await expect(storage.getByText("/data/memory/cognee", { exact: true })).toBeVisible();
  await expect(storage.getByRole("textbox")).toHaveCount(0);
  await expect(dialog.getByRole("link", { name: "View storage location in unified data management" })).toHaveAttribute("href", "/webui/data");
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("cognee-storage-settings.png") });
});
