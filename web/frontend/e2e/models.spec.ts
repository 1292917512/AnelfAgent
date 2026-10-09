import { test, expect } from "./fixtures";
import type { ModelConfig, ModelPriorityItem, ProviderConfig } from "../src/lib/types";

const provider: ProviderConfig = { id: "demo", name: "Demo provider", base_url: "https://example.test/v1",
  api_key: "masked-key", api_type: "openai", proxy_url: "", media_protocol: "", model_count: 1 };
const model: ModelConfig = { id: "demo-model", name: "", model: "remote-model", enabled: true, is_default: true, model_types: ["chat"],
  supports_vision: false, supports_video: false, supports_tools: true, supports_forced_tool_choice: true, supports_reasoning: false,
  vision_format: "", temperature: null, top_p: null, max_tokens: null, frequency_penalty: 0, presence_penalty: 0,
  timeout: 60, request_params: {}, extra_body: {}, extra_headers: {}, chat_protocol: "chat_completions", builtin_tools: [],
  input_cost: null, output_cost: null, context_window: 16000 };
const item = (id: string, is_default = false): ModelPriorityItem => ({ id, model: id, provider_id: "demo", provider_name: "Demo provider",
  enabled: true, is_default, supports_tools: true, supports_vision: false, supports_reasoning: true, api_type: "openai",
  input_cost: null, output_cost: null, context_window: 16000 });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/models/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") { await route.fulfill({ json: { status: "ok", result: "Connected" } }); return; }
    const responses: Record<string, unknown> = {
      "/api/models/providers": [provider], "/api/models/providers/demo/models": [model],
      "/api/models/providers/demo/remote-models": { models: [{ id: "remote-model" }, { id: "other-model" }] },
      "/api/models/api-types": { api_types: [{ value: "openai", group: "common", default_base_url: "" }] },
      "/api/models/priorities": { chat: [item("alpha", true), item("beta-search")] },
    };
    await route.fulfill({ json: responses[path] ?? {} });
  });
});

test("model picker supports search and keyboard selection inside another dialog", async ({ page }) => {
  await page.goto("/webui/tasks");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /Daily review|Edit Task|Edit task/ });
  const trigger = dialog.getByRole("button", { name: "Select model", exact: true });
  await trigger.click();
  const search = page.getByRole("combobox", { name: "Search models or providers" });
  await search.fill("beta");
  await search.press("ArrowDown");
  await search.press("Enter");
  await expect(trigger).toContainText("beta-search");
  await trigger.click();
  await search.press("Escape");
  await expect(dialog).toBeVisible();
  await expect(trigger).toBeFocused();
});

test("provider edits are guarded and connection tests use the edited URL", async ({ page }) => {
  await page.goto("/webui/models");
  await page.getByRole("button", { name: /Demo provider/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Provider Config", exact: true });
  await dialog.getByLabel("API URL", { exact: true }).fill("https://changed.example/v1");
  const request = page.waitForRequest((item) => item.url().endsWith("/models/test") && item.method() === "POST");
  await dialog.getByRole("button", { name: "Test", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ base_url: "https://changed.example/v1" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog.getByLabel("API URL", { exact: true })).toHaveValue("https://changed.example/v1");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test("model editor retains changes when saving fails or closing is cancelled", async ({ page }) => {
  let fails = true;
  await page.route("**/api/models/demo-model", (route) =>
    fails ? route.fulfill({ status: 500, json: { detail: "Save unavailable" } }) : route.fulfill({ json: { status: "ok" } }));
  await page.goto("/webui/models");
  await page.getByRole("button", { name: /Demo provider/ }).click();
  await page.getByRole("button", { name: /^demo-model/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).last().click();
  const dialog = page.getByRole("dialog", { name: /Edit Model Config/ });
  const identifier = dialog.getByLabel("Model ID", { exact: true });
  await identifier.fill("changed-model");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByText("Save unavailable")).toBeVisible();
  await expect(identifier).toHaveValue("changed-model");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  fails = false;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
