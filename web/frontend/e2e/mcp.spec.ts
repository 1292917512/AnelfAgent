import { test, expect } from "./fixtures";

test("MCP ordering persists in JSON, edits retain draft, tool details load on demand", async ({ page }, info) => {
  let names = ["alpha", "beta"];
  let fail = false;
  let toolsRead = 0;
  const writes: unknown[] = [];
  const config = { command: "npx", args: ["-y", "demo"], transport: "stdio", timeout: 10, env: { TOKEN: "********" } };
  await page.route("**/api/mcp/**", async (route) => {
    const path = new URL(route.request().url()).pathname.replace(/\/$/, "");
    const method = route.request().method();
    if (path.endsWith("/reorder")) {
      if (fail) { await route.fulfill({ status: 409, json: { detail: "List changed" } }); return; }
      names = route.request().postDataJSON().names as string[];
      await route.fulfill({ json: { status: "ok" } }); return;
    }
    if (path.endsWith("/config")) { await route.fulfill({ json: { content: JSON.stringify({ mcpServers: Object.fromEntries(names.map((name) => [name, config])) }) } }); return; }
    if (path.endsWith("/tools")) { toolsRead++; await route.fulfill({ json: [{ name: "demo_tool", description: "Full tool details", params: [{ name: "query", type: "string", required: true, description: "Search text" }] }] }); return; }
    if (path.endsWith("/alpha") || path.endsWith("/beta")) {
      if (method === "PUT") { writes.push(route.request().postDataJSON()); await route.fulfill({ status: 500, json: { detail: "Save failed" } }); }
      else await route.fulfill({ json: config });
      return;
    }
    if (path.endsWith("/oauth-status")) { await route.fulfill({ json: {} }); return; }
    await route.fulfill({ json: names.map((name) => ({ name, url: "npx", transport: "stdio", enabled: true, connected: true, sleeping: true, tool_count: 1 })) });
  });
  await page.goto("/webui/mcp");
  const sections = page.locator("section[aria-label=alpha], section[aria-label=beta]");
  await expect(sections.first()).toHaveAttribute("aria-label", "alpha");
  expect(toolsRead).toBe(0);
  await page.screenshot({ path: info.outputPath("mcp-list.png") });
  await page.getByRole("button", { name: "Move down · alpha", exact: true }).click();
  await expect(sections.first()).toHaveAttribute("aria-label", "beta");
  await expect(page.getByRole("button", { name: "Move up · alpha", exact: true })).toBeEnabled();
  const handle = page.getByRole("button", { name: "Reorder alpha", exact: true });
  await handle.scrollIntoViewIfNeeded();
  await handle.focus();
  await handle.press("Space");
  await expect(handle).toHaveAttribute("aria-pressed", "true");
  await handle.press("ArrowUp");
  await expect(page.getByRole("status").filter({ hasText: "over droppable area beta" })).toBeAttached();
  await handle.press("Space");
  await expect(sections.first()).toHaveAttribute("aria-label", "alpha");
  await expect(page.getByRole("button", { name: "Move down · alpha", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Move down · alpha", exact: true }).click();
  await expect(sections.first()).toHaveAttribute("aria-label", "beta");
  fail = true;
  await page.getByRole("button", { name: "Move up · alpha", exact: true }).click();
  await expect(page.getByText("List changed", { exact: true })).toBeVisible();
  await expect(sections.first()).toHaveAttribute("aria-label", "beta");
  await page.getByRole("tab", { name: "JSON Config", exact: true }).click();
  const raw = await page.getByRole("textbox", { name: "JSON Config" }).inputValue();
  expect(Object.keys(JSON.parse(raw).mcpServers)).toEqual(["beta", "alpha"]);
  await page.getByRole("tab", { name: "Servers", exact: true }).click();
  await page.getByRole("region", { name: "alpha", exact: true }).getByRole("button", { name: "1 tools" }).click();
  const tools = page.getByRole("dialog");
  await expect(tools.getByText("demo_tool")).toBeVisible();
  await expect(tools.getByText("Full tool details")).toHaveCount(0);
  await tools.getByText("demo_tool").click();
  await expect(tools.getByText("Full tool details")).toBeVisible();
  await tools.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Edit · alpha", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByText("Advanced settings", { exact: true }).click();
  await dialog.getByRole("spinbutton", { name: "Connect Timeout (s)" }).fill("20");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Save failed");
  expect(writes).toEqual([{ timeout: 20 }]);
  await expect(dialog.getByRole("spinbutton", { name: "Connect Timeout (s)" })).toHaveValue("20");
  await page.screenshot({ path: info.outputPath("mcp-editor.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
