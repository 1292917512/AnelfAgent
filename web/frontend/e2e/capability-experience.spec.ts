import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";

test("Cognee waits for model setup before loading datasets", async ({ page }, info) => {
  let ready = false;
  let datasetReads = 0;
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready, configuration_required: !ready, version: "1.6.2", reason: ready ? "" : "Configure chat and embedding models" },
    resolved: {}, sync: { running: true, pending: 2, failed: 0, synced: 0, last_error: "" },
  } }));
  await page.route("**/api/memory/probe-status", (route) => route.fulfill({ json: { enabled: true, inject_enabled: true, counters: {}, scopes: [] } }));
  await page.route("**/api/memory/cognee/config", (route) => route.fulfill({ json: {
    enabled: true, sync_enabled: true, recall_enabled: true,
    chat: { source: "auto", model_id: "", extra_args: {} },
    embedding: { source: "auto", model_id: "" },
  } }));
  await page.route("**/api/memory/cognee/datasets", (route) => {
    datasetReads++;
    return route.fulfill({ json: [{ id: "dataset-1", name: "Global memories" }] });
  });
  await page.goto("/webui/memory?tab=cognee");
  await expect(page.getByText("Awaiting configuration", { exact: true })).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Compact Storage", exact: true })).toBeDisabled();
  expect(datasetReads).toBe(0);
  ready = true;
  await page.reload();
  await expect(page.getByText("Global memories", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Improve graph", exact: true })).toBeEnabled();
  expect(datasetReads).toBe(1);
  await page.screenshot({ path: info.outputPath("cognee-ready.png"), fullPage: true });
});
import type { CapabilityStatus, ConfigMetaItem } from "../src/lib/types";

test("Cognee reasoning edits survive failures and independent saves", async ({ page }, info) => {
  let rejectSave = true;
  let pending = false;
  let config = {
    enabled: true, sync_enabled: true, recall_enabled: true,
    chat: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", api_version: "", instructor_mode: "", max_completion_tokens: 0, reasoning_effort: "", extra_args: {} },
    embedding: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", dimensions: 0 },
    options: { chat_providers: ["openai"], embed_providers: ["openai"], instructor_modes: [""], reasoning_efforts: ["", "off", "low", "high"] },
  };
  await page.route("**/api/memory/cognee/config", async (route) => {
    if (route.request().method() === "PUT") {
      if (rejectSave) return route.fulfill({ status: 400, json: { detail: "Thinking budget must be lower than the output limit" } });
      config = { ...config, ...route.request().postDataJSON() };
      pending = true;
    }
    return route.fulfill({ json: config });
  });
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready: true, configuration_required: false, version: "1.6.4", reason: "" },
    resolved: { chat: { provider: "openai", model: "openai/glm-test" } },
    sync: { running: true, pending: 1, failed: 0, synced: 3, last_error: "", configuration_pending: pending },
  } }));
  await page.route("**/api/memory/probe-status", (route) => route.fulfill({ json: { enabled: true, inject_enabled: true, counters: {}, scopes: [] } }));
  await page.route("**/api/memory/cognee/datasets", (route) => route.fulfill({ status: 503, json: { detail: "Dataset store temporarily unavailable" } }));
  await page.goto("/webui/memory?tab=cognee");
  const chat = page.locator("section").filter({ has: page.getByRole("heading", { name: "Extraction Model", exact: true }) });
  const embedding = page.locator("section").filter({ has: page.getByRole("heading", { name: "Embedding Model", exact: true }) });
  const effort = chat.getByRole("combobox", { name: "Reasoning effort", exact: true });
  await effort.selectOption("off");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect(chat.getByRole("alert")).toHaveText("Thinking budget must be lower than the output limit");
  await expect(effort).toHaveValue("off");
  rejectSave = false;
  await embedding.getByRole("button", { name: "Custom", exact: true }).click();
  await embedding.getByLabel("Model identifier").fill("openai/embedding-test");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect(chat.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  await expect(embedding.getByLabel("Model identifier")).toHaveValue("openai/embedding-test");
  await expect(page.getByRole("status").filter({ hasText: "Configuration saved" })).toBeVisible();
  await effort.selectOption("high");
  await embedding.getByRole("button", { name: "Save", exact: true }).click();
  await expect(embedding.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  await expect(effort).toHaveValue("high");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => config.chat.reasoning_effort).toBe("high");
  await expect(page.getByText("Dataset store temporarily unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("openai/glm-test", { exact: true })).toBeVisible();
  await chat.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await chat.screenshot({ path: info.outputPath("cognee-model-card.png") });
  await page.screenshot({ path: info.outputPath("cognee-config.png"), fullPage: true });
});

test("capability editing preserves automatic chains and failed edits", async ({ page }, info) => {
  let overrides: Record<string, string[]> = {};
  const defaults = { tts: ["models", "minimax"], voice_mgmt: ["minimax"], music: ["models"] };
  await page.route("**/api/audio/capabilities", (route) => route.fulfill({ json: {
    providers: [
      { name: "models", capabilities: ["tts", "music"], configured: { tts: true, music: true }, details: {} },
      { name: "minimax", capabilities: ["tts", "voice_mgmt"], configured: { tts: true, voice_mgmt: true }, details: {} },
    ], defaults, overrides, chains: { ...defaults, ...overrides },
  } satisfies CapabilityStatus }));
  const writes: Record<string, string[]>[] = [];
  let fail = true;
  await page.route("**/api/config/meta/sound_provider_priority", async (route) => {
    const body: { value: Record<string, string[]> } = route.request().postDataJSON();
    writes.push(body.value);
    if (fail) { await route.fulfill({ status: 503, json: { detail: "Configuration storage unavailable" } }); return; }
    overrides = body.value;
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/webui/sound?tab=generation");
  await page.getByRole("button", { name: "Move MiniMax up" }).and(page.locator(":enabled")).click();
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Configuration storage unavailable" }).first()).toBeVisible();
  expect(writes[0]).toEqual({ tts: ["minimax", "models"] });
  fail = false;
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByText("Priority changes are not saved")).toHaveCount(0);
  await page.getByRole("button", { name: "Restore automatic" }).click();
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByText("Priority changes are not saved")).toHaveCount(0);
  expect(writes[writes.length - 1]).toEqual({});
  await page.screenshot({ path: info.outputPath("capability-chains.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("vision preview targets sources and metadata preserves non-numeric config", async ({ page }, info) => {
  const now = Date.now() / 1000;
  const frame = { path: "workspace/uploads/camera.png", source: "external:desk", captured_at: now, width: 640, height: 360 };
  await page.route("**/api/vision/status", (route) => route.fulfill({ json: {
    watching: [], sources: {}, latest: frame, frames: { [frame.source]: frame }, last_change_at: now,
    injection: { active: true, provider: "vision", last_text_inject_at: now, last_media_inject: null },
  } }));
  await page.route("**/api/vision/sources", (route) => route.fulfill({ json: { sources: [
    { key: frame.source, display_name: "Desk camera", description: "Workspace camera", pollable: false, can_capture: false, enabled: true, watching: false },
  ] } }));
  const imageUrls: URL[] = [];
  await page.route("**/api/vision/latest?*", (route) => {
    imageUrls.push(new URL(route.request().url()));
    return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#24364b"/><rect x="80" y="60" width="480" height="240" rx="16" fill="#344b66"/><text x="320" y="188" text-anchor="middle" fill="#dce7f5" font-family="sans-serif" font-size="24">Desk camera · preview</text></svg>' });
  });
  const defaults: ConfigMetaItem = { key: "vision_disabled_sources", type: "string", description: "Disabled sources", value: "external:old", default: "", editable: true, options: [], advanced: false, min: null, max: null, step: null, unit: "", tag: "", source: "config_manager", value_source: "configured", environment_variable: null };
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "vision", items: [defaults] }] } }));
  const writes: unknown[] = [];
  await page.route("**/api/config/meta/vision_disabled_sources", (route) => {
    const body: { value: unknown } = route.request().postDataJSON(); writes.push(body.value);
    return route.fulfill({ json: { status: "ok", value: body.value } });
  });
  await page.goto("/webui/vision");
  await expect(page.getByAltText("Latest frame")).toBeVisible();
  await expect.poll(() => imageUrls.length).toBeGreaterThan(0);
  expect(imageUrls[0]?.searchParams.get("source")).toBe("external:desk");
  expect(imageUrls[0]?.searchParams.has("v")).toBe(true);
  await page.screenshot({ path: info.outputPath("vision-overview.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(accessibility.violations.map((item) => item.id)).toEqual([]);
  await page.getByRole("tab", { name: "Config", exact: true }).click();
  const input = page.getByRole("textbox", { name: "Disabled sources", exact: true });
  await expect(input).toHaveValue("external:old");
  await input.fill("external:desk");
  await input.press("Enter");
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toBe("external:desk");
});

test("audio overview loads recordings on demand and shows probe errors", async ({ page }, info) => {
  await page.route("**/api/audio/status", (route) => route.fulfill({ json: {
    providers: [
      { name: "Transcription service", kind: "asr", available: true, priority: 1, state: "ready" },
      { name: "Identity service", kind: "voiceprint", available: false, priority: 2, state: "timeout", hint: "Status probe timed out; retry to check availability." },
    ], asr_available: true, voiceprint_available: false, tts_providers: [{ name: "Speech synthesis", available: true, priority: 1, state: "ready" }], tts_available: true,
    realtime: { enabled: true, mode: "cascade", sessions: 0, owners: [], states: {} }, voice_config: {},
    library: { segments: 152, speakers: 6, pending_speakers: 2, bound_speakers: 4, unread_segments: 12, audio_ms: 720000, db_path: "config/memory/audio.sqlite3" },
    injection: { provider: "audio_status", active: true },
  } }));
  let requests = 0;
  await page.route("**/api/audio/recordings?*", (route) => {
    requests++;
    const offset = Number(new URL(route.request().url()).searchParams.get("offset"));
    return route.fulfill({ json: { total: 13, items: Array.from({ length: offset ? 1 : 12 }, (_, i) => ({
      path: "workspace/uploads/recordings/" + (offset + i) + ".wav", status: "done", file_count: 1, segments: 3, error: "",
    })) } });
  });
  await page.goto("/webui/sound");
  await expect(page.getByText("Probe timed out", { exact: true })).toBeVisible();
  expect(requests).toBe(0);
  await page.screenshot({ path: info.outputPath("audio-overview.png"), fullPage: true });
  await page.getByRole("button", { name: "Browse recordings", exact: true }).click();
  await expect(page.getByText("workspace/uploads/recordings/0.wav", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Load more", exact: true }).click();
  await expect(page.getByText("workspace/uploads/recordings/12.wav", { exact: true })).toBeVisible();
  expect(requests).toBe(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("tag purposes link to tools and retrieval failures stay visible", async ({ page }, info) => {
  await page.route("**/api/tags/unified", (route) => route.fulfill({ json: [
    { name: "speaker_scope", description: "Speaker identity and conversation origin", builtin: true, sources: ["message"], message_key: true, tool_count: 0, tools: [] },
    { name: "media:image", description: "Image understanding and generation", builtin: true, sources: ["tool"], message_key: false, tool_count: 2, tools: ["recognize_image", "generate_image"] },
    { name: "custom_ops", description: "Personal maintenance tools", builtin: false, sources: ["custom", "tool", "message"], message_key: true, tool_count: 1, tools: ["check_service"] },
  ] }));
  await page.goto("/webui/tags");
  await expect(page.getByRole("button", { name: "Delete tag custom_ops" })).toBeDisabled();
  await page.getByRole("textbox", { name: "Search tags, descriptions or linked tools…" }).fill("recognize_image");
  await expect(page.getByRole("link", { name: "2 linked tools" })).toHaveAttribute("href", "/webui/tools?tag=media%3Aimage");
  await page.screenshot({ path: info.outputPath("tag-catalog.png"), fullPage: true });
  await page.route("**/api/retrieval/matrix", (route) => route.fulfill({ json: {
    capabilities: ["search", "reader", "repo"], selection: { search: "auto", reader: "auto", repo: "auto" }, active: { search: "demo", reader: "builtin" },
    providers: [
      { name: "demo", display_name: "Research service", description: "Search across published sources", enabled: true, configured: true, requires_credential: true, credential_source: "config", capabilities: ["search"] },
      { name: "builtin", display_name: "Web reader", description: "Read and extract web documents", enabled: true, configured: true, requires_credential: false, credential_source: "", capabilities: ["reader"] },
    ],
  } }));
  await page.route("**/api/retrieval/providers/demo/test", (route) => route.fulfill({ json: { ok: false, latency_ms: 12, error: "Search quota exceeded" } }));
  await page.goto("/webui/retrieval");
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Research service", exact: true }) });
  await card.locator("summary").click();
  await card.getByRole("button", { name: "Test", exact: true }).click();
  await expect(card.getByText("Search quota exceeded")).toBeVisible();
  await page.screenshot({ path: info.outputPath("retrieval-providers.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
