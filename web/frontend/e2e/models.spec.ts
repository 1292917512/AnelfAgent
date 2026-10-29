import { test, expect } from "./fixtures";
import AxeBuilder from "@axe-core/playwright";
import type { ModelConfig, ModelPriorityItem, ProviderConfig } from "../src/lib/types";

const provider: ProviderConfig = { id: "demo", name: "Demo provider", base_url: "https://example.test/v1",
  api_key: "masked-key", api_type: "openai", proxy_mode: "system", media_protocol: "", model_count: 1 };
const model: ModelConfig = { id: "demo-model", name: "", model: "remote-model", enabled: true, is_default: true, model_types: ["chat"],
  supports_vision: false, supports_video: false, supports_tools: true, supports_forced_tool_choice: true, supports_reasoning: false,
  vision_format: "base64", temperature: null, top_p: null, max_tokens: null, frequency_penalty: 0, presence_penalty: 0,
  timeout: 60, request_params: {}, extra_body: {}, extra_headers: {}, chat_protocol: "chat_completions", builtin_tools: [],
  input_cost: null, output_cost: null, context_window: 16000 };
const item = (id: string, is_default = false): ModelPriorityItem => ({ id, model: id, provider_id: "demo", provider_name: "Demo provider",
  enabled: true, is_default, supports_tools: true, supports_vision: false, supports_reasoning: true, api_type: "openai",
  input_cost: null, output_cost: null, context_window: 16000 });

test.beforeEach(async ({ page }) => {
  await page.route("**/api/models/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/models/model-info/batch") { await route.fulfill({ json: { info: {} } }); return; }
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

test("missing chat models guide to setup and the guide disappears after a model is added", async ({ page }, info) => {
  let configured = false;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/models/priorities", (route) => route.fulfill({ json: configured ? { chat: [item("first-chat-model", true)] } : {} }));
  await page.route("**/api/models/providers", (route) => route.fulfill({ json: [{ ...provider, model_count: configured ? 1 : 0 }] }));
  await page.route("**/api/models/providers/demo/models", async (route) => {
    if (route.request().method() === "POST") {
      configured = true;
      await route.fulfill({ json: { ...model, id: "first-chat-model" } });
    } else await route.fulfill({ json: configured ? [{ ...model, id: "first-chat-model" }] : [] });
  });
  await page.goto("/webui/");
  const guide = page.getByRole("complementary", { name: "Set up a chat model" });
  await expect(guide).toBeVisible();
  expect((await new AxeBuilder({ page }).include("aside[aria-labelledby]").analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("model-setup-guide.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await guide.getByRole("link", { name: "Configure models" }).click();
  await expect(page).toHaveURL(/\/webui\/models$/);
  await expect(guide).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Model Config", exact: true })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: /Demo provider/ }).click();
  await page.getByRole("button", { name: "Manual Add", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Add Model Manually", exact: true });
  await dialog.getByRole("textbox", { name: "Model ID", exact: true }).fill("first-chat-model");
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  const refreshed = page.waitForResponse("**/api/models/priorities");
  await page.goBack();
  await refreshed;
  await expect(guide).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("manual model dialog aligns actions and animates guarded close and successful save", async ({ page, isMobile }, info) => {
  await page.goto("/webui/models");
  await page.evaluate(() => document.addEventListener("animationstart", (event) => {
    if (event instanceof AnimationEvent && event.animationName === "surface-exit" && event.target instanceof HTMLElement) {
      document.documentElement.dataset.dialogExits = (document.documentElement.dataset.dialogExits ?? "") + event.target.textContent;
    }
  }));
  await page.getByRole("button", { name: /Demo provider/ }).click();
  const trigger = page.getByRole("button", { name: "Manual Add", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Add Model Manually", exact: true });
  await dialog.getByRole("textbox", { name: "Model ID", exact: true }).fill("draft-model");
  const actions = dialog.locator(".dialog-actions > button");
  const boxes = await actions.evaluateAll((buttons) => buttons.map((button) => {
    const { x, y, width, height } = button.getBoundingClientRect();
    return { x, y, width, height };
  }));
  expect(boxes).toHaveLength(2);
  expect(boxes[0]!.y).toBeCloseTo(boxes[1]!.y, 0);
  expect(boxes[0]!.height).toBe(boxes[1]!.height);
  expect(boxes[0]!.height).toBeGreaterThanOrEqual(isMobile ? 44 : 40);
  expect(boxes[0]!.x + boxes[0]!.width).toBeLessThan(boxes[1]!.x);
  await page.screenshot({ path: info.outputPath("manual-model-actions.png"), animations: "disabled" });
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  const confirmation = page.getByRole("dialog", { name: "Unsaved changes", exact: true });
  await confirmation.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Model ID", exact: true })).toHaveValue("draft-model");
  await expect(dialog.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await confirmation.getByRole("button", { name: "Discard changes", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.dialogExits)).toContain("Add Model Manually");
  await trigger.click();
  await expect(dialog.getByRole("textbox", { name: "Model ID", exact: true })).toHaveValue("");
  await dialog.getByRole("textbox", { name: "Model ID", exact: true }).fill("saved-model");
  await dialog.getByRole("button", { name: "Create", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(trigger).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await trigger.click();
  await expect(dialog).toHaveCSS("animation-name", "none");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).toBeHidden();
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
  await dialog.getByLabel("Network policy", { exact: true }).selectOption("proxy");
  await expect(dialog.getByRole("link", { name: "Global proxy and routing rules" })).toHaveAttribute("href", "/webui/config?key=proxy_enabled");
  const request = page.waitForRequest((item) => item.url().endsWith("/models/test") && item.method() === "POST");
  await dialog.getByRole("button", { name: "Test", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ base_url: "https://changed.example/v1", proxy_mode: "proxy" });
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

for (const browseFirst of [true, false]) test(`remote model cache stays consistent with ${browseFirst ? "browsing" : "editing"} first`, async ({ page }) => {
  if (browseFirst) await page.route("**/api/models/model-info/batch", (route) => route.fulfill({ status: 503, json: { detail: "Metadata unavailable" } }));
  await page.goto("/webui/models");
  await page.getByRole("button", { name: /Demo provider/ }).click();
  if (browseFirst) {
    await page.getByRole("button", { name: "Browse Remote Models", exact: true }).click();
    await expect(page.getByText("other-model", { exact: true })).toBeVisible();
  }
  await page.getByRole("button", { name: /^demo-model/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).last().click();
  const dialog = page.getByRole("dialog", { name: /Edit Model Config/ });
  await expect(dialog.getByLabel("Model ID", { exact: true })).toHaveValue("remote-model");
  if (!browseFirst) {
    const response = page.waitForResponse((res) => res.url().endsWith("/remote-models"));
    await dialog.getByRole("button", { name: "Fetch remote models", exact: true }).click();
    await response;
  }
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  if (!browseFirst) await page.getByRole("button", { name: "Browse Remote Models", exact: true }).click();
  await expect(page.getByText("other-model", { exact: true })).toBeVisible();
});

test("GLM editing preserves its contract without depending on remote discovery", async ({ page }, testInfo) => {
  const thinking = { param: "reasoning_effort", map: { low: "low", medium: "high", high: "high", xhigh: "max", max: "max" }, off: "low" };
  const glm: ModelConfig = { ...model, model: "glm-5.3", context_window: 1000000,
    supports_reasoning: true, supports_forced_tool_choice: false, chat_protocol: "responses", reasoning_effort: "max", thinking };
  await page.route("**/api/models/providers/demo/models", (route) => route.fulfill({ json: [glm] }));
  let remoteRequests = 0;
  await page.route("**/api/models/providers/demo/remote-models", (route) => {
    remoteRequests++;
    return route.fulfill({ status: 503, json: { detail: "Discovery unavailable" } });
  });
  await page.route("**/api/models/test-chat", (route) => route.fulfill({ json: { ok: true, ttft_ms: 25, total_ms: 100, output_tokens: 8 } }));
  await page.goto("/webui/models");
  await page.getByRole("button", { name: /Demo provider/ }).click();
  await page.getByRole("button", { name: /^demo-model/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).last().click();
  const dialog = page.getByRole("dialog", { name: /Edit Model Config/ });
  await expect(dialog.getByLabel("Model ID", { exact: true })).toHaveValue("glm-5.3");
  await expect(dialog.getByLabel("Thinking Level", { exact: true })).toHaveValue("max");
  expect(remoteRequests).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("glm-editor.png"), animations: "disabled" });
  const bounds = await dialog.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
  await dialog.getByRole("button", { name: "Fetch remote models", exact: true }).click();
  await expect(dialog.getByText("The remote list is unavailable. You can still edit and save this model.")).toBeVisible();
  await dialog.getByRole("button", { name: /Advanced request settings/ }).click();
  await dialog.getByLabel("top_p", { exact: true }).fill("2");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("top_p must be between 0 and 1");
  await dialog.getByLabel("top_p", { exact: true }).fill("0.7");
  const request = page.waitForRequest((req) => req.url().endsWith("/models/demo-model") && req.method() === "PUT");
  await dialog.getByRole("button", { name: "Save & Test", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ model: "glm-5.3", context_window: 1000000,
    max_tokens: null, temperature: null, top_p: 0.7, chat_protocol: "responses", reasoning_effort: "max", thinking,
    supports_forced_tool_choice: false });
  await expect(dialog.getByText("Test Passed", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});
