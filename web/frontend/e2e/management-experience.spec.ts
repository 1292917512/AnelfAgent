import AxeBuilder from "@axe-core/playwright";
import { test, expect, openWebChat } from "./fixtures";
import type { ConfigMetaItem, ToolItem } from "../src/lib/types";

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
