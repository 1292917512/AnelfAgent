import { test, expect } from "./fixtures";
import AxeBuilder from "@axe-core/playwright";
import type { ProviderKeyEntry } from "../src/lib/types";

test("credential cards preserve secrets and drafts, batch save and clear explicitly", async ({ page }, info) => {
  const entries: ProviderKeyEntry[] = [
    { name: "sample", title: "Example platform", description: "Voice and image service credentials.", domain: "sound", domains: ["sound", "vision"],
      owner: "entity:sample", destination: "/entities/sample", editor: "", configured: true,
      fields: [{ key: "api_key", label: "API Key", value: "test…1234", secret: true, configured: true, required: true, multiline: false },
        { key: "endpoint", label: "Service URL", value: "https://api.example.test/v1", secret: false, configured: true, required: false, multiline: false }] },
    { name: "github", title: "GitHub App", description: "Private key for channel authentication. ".repeat(12), domain: "channel", domains: ["channel"],
      owner: "channel:github", destination: "/channels?channel=github", editor: "", configured: false,
      fields: [{ key: "api_key", label: "Private key (PEM)", value: "", secret: true, configured: false, required: true, multiline: true }] },
    { name: "face", title: "Face service", description: "Self-hosted recognition endpoint.", domain: "vision", domains: ["vision"],
      owner: "core:agent.vision.face", destination: "/vision?tab=face", editor: "", configured: false,
      fields: [{ key: "endpoint", label: "Service URL", value: "", secret: false, configured: false, required: true, multiline: false }] },
  ];
  const writes: Record<string, string>[] = [];
  let fail = false;
  await page.route("**/api/provider-keys**", async (route) => {
    if (route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { values: Record<string, string> };
      if (fail) { await route.fulfill({ status: 503, json: { detail: "Storage unavailable" } }); return; }
      writes.push(body.values);
      const entry = entries.find((item) => new URL(route.request().url()).pathname.endsWith(`/${item.name}`))!;
      for (const field of entry.fields) if (field.key in body.values) {
        field.configured = Boolean(body.values[field.key]);
        field.value = field.secret ? field.configured ? "new…5678" : "" : body.values[field.key] ?? "";
      }
      await route.fulfill({ json: { ok: true } });
    } else await route.fulfill({ json: { providers: entries } });
  });
  await page.goto("/webui/models?tab=providerKeys");
  const card = page.getByRole("region", { name: "Example platform" });
  await expect(card).toBeVisible();
  await expect(page.getByLabel("API Key", { exact: true })).toHaveCount(0);
  const heights = await page.locator("section[id^='credential-']").evaluateAll((cards) => cards.map((item) => item.getBoundingClientRect().height));
  expect(heights).toHaveLength(3);
  expect(Math.max(...heights) - Math.min(...heights)).toBeLessThan(1);
  await page.screenshot({ path: info.outputPath("credentials.png"), fullPage: true, animations: "disabled" });
  await page.getByRole("tab", { name: "Channels", exact: true }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole("tab", { name: "All", exact: true }).click();
  await card.getByRole("button").click();
  const dialog = page.getByRole("dialog", { name: "Example platform", exact: true });
  const secret = dialog.getByLabel("API Key", { exact: true });
  await expect(secret).toHaveValue("");
  await expect(secret).toHaveAttribute("placeholder", "test…1234");
  await expect(dialog.getByLabel("Service URL", { exact: true })).toHaveValue("https://api.example.test/v1");
  await expect(page.getByRole("region", { name: "Face service" }).getByLabel("API Key")).toHaveCount(0);
  await page.screenshot({ path: info.outputPath("credential-editor.png"), animations: "disabled" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await secret.fill("new-test-secret");
  await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(secret).toHaveValue("new-test-secret");
  fail = true;
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("Storage unavailable");
  await expect(secret).toHaveValue("new-test-secret");
  fail = false;
  await dialog.getByLabel("Service URL", { exact: true }).fill("https://updated.example.test");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(secret).toHaveValue("");
  expect(writes).toEqual([{ api_key: "new-test-secret", endpoint: "https://updated.example.test" }]);
  await dialog.getByRole("button", { name: "Clear API Key", exact: true }).click();
  await expect(dialog.getByText("Cleared when saved")).toBeVisible();
  expect(writes).toHaveLength(1);
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Clear API Key", exact: true })).toHaveCount(0);
  expect(writes[1]).toEqual({ api_key: "" });
  await secret.fill("discard-this-secret");
  await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.getByRole("button", { name: "Discard changes", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("API Key", { exact: true })).toHaveCount(0);
  await card.getByRole("button").click();
  await expect(secret).toHaveValue("");
  await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
  await page.addInitScript(() => { localStorage.setItem("i18nextLng", "zh"); localStorage.setItem("theme", "dark"); });
  await page.reload();
  await expect(page.getByRole("heading", { name: "组件凭据", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveClass(/dark/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(accessibility.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.failureSummary) }))).toEqual([]);
  await page.screenshot({ path: info.outputPath("credentials-dark.png"), fullPage: true, animations: "disabled" });
});
