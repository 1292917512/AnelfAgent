import AxeBuilder from "@axe-core/playwright";
import { test, expect, openWebChat } from "./fixtures";
import type { ConfigMetaItem, RecallTestResult, ToolItem } from "../src/lib/types";

test("managed databases show separate engines and route recovery to their shared volume", async ({ page }, info) => {
  let measured = false;
  const forbiddenRequests: string[] = [];
  await page.clock.install();
  await page.route("**/api/database/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace("/api/database", "");
    if (path === "/databases") {
      await route.fulfill({ json: { items: [
        { id: "cognee", name: "Cognee SQLite", description: "Metadata", engine: "sqlite", capabilities: ["browse", "query"], size_bytes: 1024, table_count: 1 },
        { id: "cognee_graph", name: "Ladybug", description: "Graph index", engine: "ladybug", capabilities: [], size_bytes: measured ? 2048 : null, table_count: null },
        { id: "cognee_vector", name: "LanceDB", description: "Vector index", engine: "lancedb", capabilities: [], size_bytes: 4096, table_count: null },
      ].map((entry) => ({ path: "/data/cognee/system/databases", exists: true, external: false, volume_id: "cognee",
        managed_by: "Cognee", management_url: "/memory?tab=cognee", measured_at: measured ? 1700000000 : null, ...entry })) } });
    } else if (path === "/connections") await route.fulfill({ json: { items: [] } });
    else if (path === "/cognee/tables") await route.fulfill({ json: { items: [{ name: "documents", readonly: true, type: "table", virtual: false, shadow: false, row_count: 1, column_count: 1 }] } });
    else if (path === "/cognee/tables/documents/rows") await route.fulfill({ json: {
      items: [{ __rowid__: 1, values: { content: "Retained source" } }], columns: [{ cid: 0, name: "content", type: "TEXT", notnull: false, default: null, pk: false }],
      total: 1, page: 1, page_size: 50, pages: 1, readonly: true,
    } });
    else {
      forbiddenRequests.push(path);
      await route.fulfill({ status: 403, json: { detail: "Unsupported operation" } });
    }
  });
  await page.goto("/webui/data");
  await page.getByRole("button", { name: /^Ladybug/ }).click();
  const graph = page.getByRole("region", { name: "Ladybug", exact: true });
  await expect(graph).toContainText("Not measured");
  await expect(page.getByRole("tab", { name: "Query" })).toHaveCount(0);
  await expect(graph.getByRole("link", { name: "Open Cognee management" })).toHaveAttribute("href", "/webui/memory?tab=cognee");
  await expect(graph.getByRole("link", { name: "View volume backup and recovery" })).toHaveAttribute("href", "/webui/data?tab=volumes&volume=cognee");
  measured = true;
  await page.clock.fastForward(31000);
  await expect(graph).toContainText("2.0 KB");
  await page.getByRole("button", { name: /^LanceDB/ }).click();
  await expect(page.getByRole("region", { name: "LanceDB", exact: true })).toContainText("4.0 KB");
  await page.screenshot({ path: info.outputPath("managed-databases.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: /^Cognee SQLite/ }).click();
  await page.getByRole("button", { name: /documents/ }).click();
  await expect(page.getByText("Retained source", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Insert row" })).toHaveCount(0);
  expect(forbiddenRequests).toEqual([]);
});

test("recall provenance is compact and separates subjects from source conversations", async ({ page }, info) => {
  const result: RecallTestResult = {
    plan: { queries: ["quiet rooms"], entities: [], node_keys: [], node_labels: [], deep_needed: false, rationale: "" },
    depth: "shallow", cognee: { enabled: false, recall_enabled: false, datasets: [], search_types: [] },
    items: [{ id: "mem:7", source: "memory", content: "Prefers a quiet room", type: "semantic", score: .9,
      tags: ["user:telegram:subject"], dataset: "", path: "", sensitivity: "private", timestamp: 1700000000,
      provenance: { origins: [{ kind: "conversation_summary", scope: "group_qq:source", message_ids: [41, 42] }] },
    }], relations: [], forgotten: [], timings: {},
  };
  await page.route("**/api/memory/recall-test", (route) => route.fulfill({ json: result }));
  await page.goto("/webui/memory?tab=cognee");
  await page.getByRole("button", { name: "Recall validation", exact: true }).click();
  await page.getByPlaceholder(/Enter query text to test/).fill("quiet rooms");
  await page.getByRole("button", { name: "Run Test", exact: true }).click();
  await expect(page.getByText("Prefers a quiet room", { exact: true })).toBeVisible();
  await expect(page.getByText("group_qq:source", { exact: true })).toHaveCount(0);
  const control = page.locator("summary").filter({ hasText: "Attribution & provenance" });
  await control.click();
  await expect(page.getByText("group_qq:source", { exact: true })).toBeVisible();
  await expect(page.getByText(/Source message IDs: 41, 42/)).toBeVisible();
  await page.screenshot({ path: info.outputPath("recall-attribution.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await control.click();
  await expect(page.getByText("group_qq:source", { exact: true })).toHaveCount(0);
});

test("document indexing reports partial coverage and allows retry after failure", async ({ page }, info) => {
  await page.route("**/api/memory/health", (route) => route.fulfill({ json: { type_counts: {}, total_memories: 0 } }));
  await page.route("**/api/memory/index/status", (route) => route.fulfill({ json: { files: 3, chunks: 6 } }));
  let release: () => void = () => {};
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let fail = false;
  await page.route("**/api/memory/index/resync?*", async (route) => {
    await pending;
    await route.fulfill({ json: fail ? { error: "Index unavailable" } : { synced: 3, unchanged: 2, removed: 1, failed: 1, chunks: 6 } });
  });
  await page.goto("/webui/memory");
  const sync = page.getByRole("button", { name: "Incremental Sync", exact: true });
  await sync.click();
  await expect(sync).toBeDisabled();
  await expect(page.getByRole("button", { name: "Full Rebuild", exact: true })).toBeDisabled();
  await expect(page.getByRole("status")).toContainText("Synchronizing document index");
  release();
  await expect(page.getByRole("status")).toContainText("Synced 3 · Unchanged 2 · Removed 1 · Failed 1");
  await expect(sync).toBeEnabled();
  await page.screenshot({ path: info.outputPath("document-index-coverage.png") });
  fail = true;
  await sync.click();
  await expect(page.getByRole("alert")).toContainText("Index synchronization failed");
  fail = false;
  await sync.click();
  await expect(page.getByRole("status")).toContainText("Synced 3");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("tool filters survive navigation and editing failures preserve the draft", async ({ page }, info) => {
  const tools: ToolItem[] = [
    { name: "inspect_service", description: "Inspect service health and summarize recent events.", tags: ["ops", "read"], enabled: true, source: "entity" },
    { name: "restart_service", description: "Restart the service after checking the current state.", tags: ["ops"], enabled: false, source: "entity" },
  ];
  await page.route("**/api/tools/grouped", (route) => route.fulfill({ json: [{ group: "demo", description: "Service operations", tools,
    enabled_count: tools.filter((tool) => tool.enabled).length, total_count: 2, all_enabled: tools.every((tool) => tool.enabled), any_enabled: tools.some((tool) => tool.enabled) }] }));
  await page.route("**/api/tools/plugins", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/tags/tool", (route) => route.fulfill({ json: ["ops", "read"] }));
  await page.route("**/api/tags/unified", (route) => route.fulfill({ json: [{ name: "ops" }, { name: "read" }] }));
  let fail = true;
  await page.route("**/api/tools/inspect_service/meta", (route) => fail
    ? route.fulfill({ status: 503, json: { detail: "Tool metadata storage unavailable" } })
    : route.fulfill({ json: { status: "ok" } }));
  await page.goto("/webui/tools");
  await page.getByRole("combobox", { name: "Tool status" }).selectOption("enabled");
  await page.getByRole("button", { name: "Tags", exact: true }).click();
  await page.getByRole("button", { name: "read", exact: true }).click();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("heading", { name: "inspect_service", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "restart_service", exact: true })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("tools-filtered.png") });
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }))).toEqual([]);
  await page.getByRole("button", { name: "Open entity details: demo" }).click();
  await page.goBack();
  await expect(page.getByRole("combobox", { name: "Tool status" })).toHaveValue("enabled");
  await expect(page.getByRole("button", { name: "Remove tag: read" })).toBeVisible();
  await page.getByRole("button", { name: "Edit Properties: inspect_service" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit Tool Properties" });
  await dialog.getByRole("textbox", { name: "Tool Description" }).fill("A precise description worth keeping");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("Tool metadata storage unavailable");
  await expect(dialog.getByRole("textbox", { name: "Tool Description" })).toHaveValue("A precise description worth keeping");
  await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(dialog.getByRole("textbox", { name: "Tool Description" })).toHaveValue("A precise description worth keeping");
  fail = false;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("entity configuration uses registered field types and retries failed saves in place", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  const defaults: ConfigMetaItem = { key: "demo_address", description: "Service address", type: "string", value: "https://service.example", default: "", editable: true,
    options: null, advanced: false, min: null, max: null, step: null, unit: "", tag: "", source: "config_manager", value_source: "configured", environment_variable: null };
  const fields: ConfigMetaItem[] = [defaults,
    { ...defaults, key: "demo_mode", description: "Connection mode", type: "enum", value: "auto", default: "auto", options: ["auto", "direct"] },
    { ...defaults, key: "demo_token", description: "Service token", type: "password", value: "abcd****5678" },
    { ...defaults, key: "demo_limit", description: "Request limit", type: "range", value: 10, default: 10, min: 1, max: 50, step: 1 },
  ];
  let enabled = true;
  await page.route("**/api/entities/demo", (route) => route.fulfill({ json: { name: "demo_tool", group: "demo", type: "tool", source: "entity", enabled,
    description: "Registered capabilities and their shared configuration.", manifest: { display_name: "Service operations", version: "", description: "" },
    config_group: "entity/demo", config_items: [], configs: {}, tools: [], providers: [], apis: [] } }));
  await page.route("**/api/entities/demo/enable", async (route) => {
    enabled = !enabled;
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "entity/demo", items: fields }] } }));
  let fail = true;
  let writes = 0;
  await page.route("**/api/config/meta/demo_address", async (route) => {
    writes++;
    if (fail) { await route.fulfill({ status: 503, json: { detail: "Configuration storage unavailable" } }); return; }
    const body: { value: string } = route.request().postDataJSON();
    fields[0] = { ...defaults, value: body.value };
    await route.fulfill({ json: { status: "ok", key: defaults.key, value: body.value } });
  });
  await page.goto("/webui/entities/demo?tab=config");
  await expect(page.getByRole("combobox", { name: "Connection mode", exact: true })).toBeVisible();
  await expect(page.getByLabel("Service token", { exact: true })).toHaveAttribute("type", "password");
  await expect(page.getByRole("slider", { name: "Request limit", exact: true })).toBeVisible();
  const address = page.getByRole("textbox", { name: "Service address", exact: true });
  await address.fill("https://new.example");
  await address.press("Tab");
  await expect(page.locator("#config-item-demo_address").getByRole("alert")).toContainText("Configuration storage unavailable");
  await expect(address).toHaveValue("https://new.example");
  fail = false;
  await address.focus();
  await address.press("Enter");
  await expect(address).toBeEnabled();
  await expect(page.locator("#config-item-demo_address").getByRole("alert")).toHaveCount(0);
  expect(writes).toBe(2);
  await page.getByRole("button", { name: "Enabled", exact: true }).click();
  await expect(page.getByRole("button", { name: "Disabled", exact: true })).toBeEnabled();
  await page.screenshot({ path: info.outputPath("entity-config-dark.png") });
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((item) => ({ id: item.id, targets: item.nodes.map((node) => node.target) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("conversation resizing retains the reading anchor and unsent draft", async ({ page, isMobile }, info) => {
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: Array.from({ length: 30 }, (_, index) => ({
    id: index + 1, role: "assistant", ts: 1770000000 + index,
    content: `## Review ${index + 1}\n\nA detailed account of the current operation, the evidence gathered and the next steps. This paragraph should remain readable when the conversation is expanded.\n\n[Open note](./note.txt)`,
  })) }));
  await page.goto("/webui/");
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  const list = chat.locator(".message-list");
  await expect(list.locator("[data-message-key]")).toHaveCount(30);
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep this draft while reading");
  await list.evaluate((element) => {
    const row = element.querySelector('[data-message-key="msg-12-assistant"]');
    if (!row) throw new Error("Reading anchor missing");
    element.scrollTop += row.getBoundingClientRect().top - element.getBoundingClientRect().top;
    element.dispatchEvent(new Event("scroll"));
  });
  const anchor = list.locator('[data-message-key="msg-12-assistant"]');
  if (isMobile) {
    await chat.getByRole("button", { name: "Close", exact: true }).click();
    await openWebChat(page);
  } else {
    await chat.getByRole("button", { name: "Expand conversation", exact: true }).click();
    await expect(page.getByRole("region", { name: "Global execution", exact: true })).toHaveCount(0);
    await expect(chat.getByRole("button", { name: "Restore split view", exact: true })).toBeVisible();
  }
  await expect(anchor).toBeInViewport();
  await expect.poll(async () => anchor.evaluate((element) => Math.abs(element.getBoundingClientRect().top - element.closest(".message-list")!.getBoundingClientRect().top))).toBeLessThan(3);
  await expect(chat.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep this draft while reading");
  await page.screenshot({ path: info.outputPath("conversation-reading.png") });
  if (!isMobile) {
    await chat.getByRole("button", { name: "Restore split view", exact: true }).click();
    await expect(anchor).toBeInViewport();
    await expect(page.getByRole("region", { name: "Global execution", exact: true })).toBeVisible();
  }
});
