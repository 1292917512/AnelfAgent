import { expect, test } from "../../../../web/frontend/e2e/fixtures";

test("plugin cards expose details and marketplace selection on the capabilities page", async ({ page }) => {
  const installed = { name: "demo", display_name: "Draw", format: "dsh", enabled: true, source: "local/draw",
    version: "1.0", description: "Diagram editor", tools: [], skills: [], mcp_servers: ["drawio"] };
  const searches: string[] = [];
  await page.route("**/api/entity/plugins**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/marketplaces")) {
      await route.fulfill({ json: [
        { name: "dsh-community", enabled: true, source_type: "github-topic", plugin_count: 1 },
        { name: "community", enabled: true, source_type: "git", plugin_count: 1 },
        { name: "disabled", enabled: false, source_type: "git", plugin_count: 1 },
      ] });
    } else if (url.pathname.endsWith("/search")) {
      searches.push(url.searchParams.get("marketplace") ?? "");
      await route.fulfill({ json: { plugins: url.searchParams.get("marketplace") === "community" ? [{
        name: "Example/Draw", description: "Draw", marketplace: "community", url: "https://github.com/Example/Draw",
      }] : [] } });
    } else {
      await route.fulfill({ json: [installed] });
    }
  });
  await page.goto("/webui/plugins");
  await expect(page.getByRole("heading", { name: "Plugins", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Draw", exact: true }).click();
  const details = page.getByRole("dialog");
  await expect(details.getByText("drawio", { exact: true })).toBeVisible();
  await details.getByRole("button", { name: "Close" }).click();
  await page.getByRole("tab", { name: "Browse & Install" }).click();
  await expect(page.getByRole("combobox")).toHaveValue("dsh-community");
  await expect(page.getByText("No matching plugins")).toBeVisible();
  expect(searches).toEqual(["dsh-community"]);
  await expect(page.locator('option[value="disabled"]')).toHaveCount(0);
  await page.getByRole("combobox").selectOption("community");
  await page.getByRole("button", { name: "Example/Draw" }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "Install", exact: true })).toBeVisible();
  await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
  await page.getByRole("button", { name: "Install from Source", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("textbox").first()).toBeVisible();
});

test("disabled sources stay visible in settings and make no search requests", async ({ page }) => {
  let searches = 0;
  const source = { name: "dsh-community", enabled: false, builtin: true, pending: true, plugin_count: -1 };
  await page.route("**/api/entity/plugins**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/marketplaces")) return route.fulfill({ json: [source] });
    if (path.endsWith("/search")) searches++;
    return route.fulfill({ json: { plugins: [], errors: {} } });
  });
  await page.goto("/webui/plugins?tab=browse");
  await expect(page.getByText("No marketplace sources enabled")).toBeVisible();
  await page.getByRole("button", { name: "Configure sources" }).click();
  await expect(page.getByRole("switch", { name: /dsh-community/ })).not.toBeChecked();
  expect(searches).toBe(0);
});

test("install from source, disable, and confirm removal through plugin details", async ({ page }) => {
  const plugin = { name: "demo", display_name: "Demo package", format: "plugin", enabled: true,
    source: "local/demo", version: "1", tools: [], skills: [], mcp_servers: [] };
  let installed = false;
  await page.route("**/api/entity/plugins**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path.endsWith("/from-source")) {
      expect(req.postDataJSON().source).toBe("local/demo");
      installed = true;
      return route.fulfill({ status: 201, json: plugin });
    }
    if (path.endsWith("/toggle")) {
      plugin.enabled = req.postDataJSON().enabled;
      return route.fulfill({ json: { enabled: plugin.enabled } });
    }
    if (req.method() === "DELETE") {
      installed = false;
      return route.fulfill({ json: { removed: true, name: plugin.name } });
    }
    if (path.endsWith("/marketplaces")) return route.fulfill({ json: [] });
    if (path.endsWith("/search")) return route.fulfill({ json: { count: 0, plugins: [] } });
    return route.fulfill({ json: installed ? [plugin] : [] });
  });

  await page.goto("/webui/plugins");
  await page.getByRole("button", { name: "Browse & Install" }).click();
  await page.getByRole("button", { name: "Configure sources" }).click();
  await expect(page.getByRole("tab", { name: "Source settings" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "Subscribe Marketplace" })).toBeVisible();
  await page.getByRole("tab", { name: "Browse & Install" }).click();
  await page.getByRole("button", { name: "Install from Source" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Plugin source" }).fill(plugin.source);
  await dialog.getByRole("button", { name: "Install", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await page.getByRole("tab", { name: "Installed Plugins" }).click();
  const card = page.getByRole("button", { name: "Demo package" });
  await card.click();
  await dialog.getByRole("switch").click();
  await expect(dialog.getByRole("switch")).not.toBeChecked();
  await dialog.getByRole("button", { name: "Remove" }).click();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(card).toHaveCount(0);
  await expect(page.getByText("No plugins installed.", { exact: false })).toBeVisible();
});
