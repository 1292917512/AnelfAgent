import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ToolGroup, ToolItem } from "../src/lib/types";

test("tool sources combine with keywords, status and tags and survive detail navigation", async ({ page, isMobile }, info) => {
  if (isMobile) await page.setViewportSize({ width: 360, height: 800 });
  const items: ToolItem[] = [
    { name: "list_channels", source: "internal", origin: { kind: "core", module: "agent.channel.manage_tools" }, enabled: true, description: "List connected channels", tags: ["inspect"] },
    { name: "qq_inspect", source: "channel.qq", origin: { kind: "channel", module: "channels.qq" }, enabled: true, description: "Inspect QQ connection", tags: ["inspect"] },
    { name: "read_file", source: "entity", origin: { kind: "entity", module: "entities.filesystem.tools" }, enabled: true, description: "Read workspace file", tags: ["inspect"] },
    { name: "write_file", source: "entity", origin: { kind: "entity", module: "entities.filesystem.tools" }, enabled: false, description: "Write workspace file", tags: ["edit"] },
    { name: "remote_read", source: "mcp", origin: { kind: "external", module: "mcp:remote" }, enabled: true, description: "Remote workspace search", tags: ["inspect"] },
  ];
  const makeGroup = (group: string, tools: ToolItem[], reload_module: string | null = null): ToolGroup => ({
    group, tools, reload_module, description: "", total_count: tools.length, enabled_count: tools.filter((tool) => tool.enabled).length,
    all_enabled: tools.every((tool) => tool.enabled), any_enabled: tools.some((tool) => tool.enabled),
  });
  await page.route("**/api/tools/grouped", (route) => route.fulfill({ json: [
    makeGroup("channel_ops", items.slice(0, 2)), makeGroup("file_alias", items.slice(2, 4), "filesystem"), makeGroup("mcp:remote", items.slice(4)),
  ] }));
  await page.route("**/api/tools/plugins", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/tags/tool", (route) => route.fulfill({ json: ["inspect", "edit"] }));
  await page.route("**/api/entities/file_alias", (route) => route.fulfill({ json: {
    name: "read_file", group: "file_alias", reload_module: "filesystem", type: "tool", source: "entity", enabled: true,
    description: "Workspace files", config_group: "", tools: items.slice(2, 4), apis: [], providers: [], manifest: {},
  } }));
  await page.goto("/webui/tools");
  await expect(page.getByRole("button", { name: "Core tools", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Core tools", exact: true }).click();
  await expect(page.getByText("1 matching tools", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Reload entity/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Sync all entities", exact: true })).toHaveCount(0);
  const search = page.getByRole("textbox", { name: "Search tool name, description, tags..." });
  await search.fill("channel LIST");
  await expect(page.getByRole("heading", { name: "list_channels", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "qq_inspect", exact: true })).toHaveCount(0);
  await expect(page.getByText("1 / 2 matching", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await page.getByRole("button", { name: "Entity extensions", exact: true }).click();
  await page.getByRole("combobox", { name: "Tool status" }).selectOption("disabled");
  await search.fill("filesystem write");
  await expect(page.getByRole("heading", { name: "write_file", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Tags", exact: true }).click();
  await page.getByRole("button", { name: "edit", exact: true }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Open entity details: file_alias", exact: true }).click();
  await page.getByRole("button", { name: "Back to tools", exact: true }).click();
  await expect(search).toHaveValue("filesystem write");
  await expect(page.getByRole("button", { name: "Entity extensions", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("combobox", { name: "Tool status" })).toHaveValue("disabled");
  await expect(page.getByRole("button", { name: "Remove tag: edit", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "write_file", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("tool-catalog-sources.png"), animations: "disabled" });
  await page.getByRole("button", { name: "Clear filters", exact: true }).click();
  await page.getByRole("button", { name: "External integrations", exact: true }).click();
  await search.fill("remote");
  await expect(page.getByRole("heading", { name: "remote_read", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Reload entity/ })).toHaveCount(0);
});
