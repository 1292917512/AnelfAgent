import AxeBuilder from "@axe-core/playwright";
import type { DecisionProvider, JudgmentCatalog } from "../src/lib/types";
import { expect, test } from "./fixtures";

test("judgment providers support local models, isolated health and complete editing", async ({ page }, info) => {
  const catalog: JudgmentCatalog = {
    providers: { cloud: { name: "Cloud decision", base_url: "https://api.typesafe.ai", api_key: "********", models: { cloud: { name: "Cloud Jev", model: "jev-latest" } } } },
    default_model: "cloud", revision: "0", health: {},
  };
  let revision = 0;
  let testCalls = 0;
  const testedIds: string[] = [];
  await page.route("**/api/judgment/providers", (route) => route.fulfill({ json: catalog }));
  await page.route("**/api/judgment/providers/*", (route) => {
    const id = route.request().url().split("/").pop()!;
    if (route.request().method() === "DELETE") {
      if (catalog.providers[id]?.models[catalog.default_model]) catalog.default_model = "";
      delete catalog.providers[id];
    } else {
      const provider: DecisionProvider = route.request().postDataJSON();
      catalog.providers[id] = { ...provider, api_key: provider.api_key ? "********" : "" };
    }
    catalog.revision = String(++revision);
    return route.fulfill({ json: catalog });
  });
  await page.route("**/api/judgment/default", (route) => {
    catalog.default_model = route.request().postDataJSON().model_id;
    catalog.revision = String(++revision);
    return route.fulfill({ json: catalog });
  });
  await page.route("**/api/judgment/status", (route) => {
    const provider = Object.values(catalog.providers).find((provider) => provider.models[catalog.default_model]);
    return route.fulfill({ json: { enabled: true, channel: provider ? "typesafe" : "llm_fallback", api_key_configured: !!provider?.api_key,
      base_url: provider?.base_url ?? "", model: provider?.models[catalog.default_model]?.model ?? "",
      timeout: 30, fallback_enabled: true, fallback_model: "", fallback_effort: "low" } });
  });
  await page.route("**/api/judgment/test", (route) => {
    const id = route.request().postDataJSON().model_id || catalog.default_model;
    testedIds.push(id);
    const offline = ++testCalls === 1;
    catalog.health![id] = { status: offline ? "offline" : "ready", checked_at: Date.now() / 1000, retry_at: offline ? Date.now() / 1000 + 300 : 0, error: offline ? "Connection refused" : "", latency_ms: 40 };
    return route.fulfill({ json: { ok: !offline, source: "typesafe", model: "StartLux-Decision-27B", latency_ms: 40,
      error: offline ? "Connection refused" : undefined, cause: offline ? "network" : undefined,
      slow: false, slow_after_ms: 1000, gate_budget_ms: 2000, answers: { needed: { type: "noul", noul: 0.9 } },
      missing: [], usage: { input_tokens: 100, output_tokens: 0 } } });
  });
  await page.goto("/webui/models?tab=judgment");
  await page.getByRole("button", { name: "Add judgment provider" }).click();
  let dialog = page.getByRole("dialog", { name: "Add judgment provider" });
  await dialog.getByLabel("Provider ID", { exact: true }).fill("pc");
  await dialog.getByLabel("Provider name", { exact: true }).fill("Local workstation");
  await dialog.getByLabel("Service URL", { exact: true }).fill("http://127.0.0.1:8090/v1");
  for (const [index, id] of ["local", "backup"].entries()) {
    await dialog.getByRole("button", { name: "Add model", exact: true }).click();
    await dialog.getByLabel("Model ID (globally unique)", { exact: true }).nth(index).fill(id);
    await dialog.getByLabel("Display name (optional)", { exact: true }).nth(index).fill(index ? "Backup decision" : "Local decision");
    await dialog.getByLabel("Server model name", { exact: true }).nth(index).fill(index ? "backup-decision" : "StartLux-Decision-27B");
  }
  await page.screenshot({ path: info.outputPath("judgment-editor.png"), animations: "disabled" });
  const editorAudit = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(editorAudit.violations).toEqual([]);
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const local = page.getByRole("article", { name: "Local workstation" });
  await expect(local.getByText("No authentication", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Default judgment model")).toHaveValue("cloud");
  await local.getByRole("button", { name: "Test", exact: true }).first().click();
  dialog = page.getByRole("dialog", { name: "Test connection & speed · local" });
  await dialog.getByRole("button", { name: "Test connection & speed", exact: true }).click();
  await expect(dialog.getByText("Test failed", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(local.getByText("Unavailable · skipped", { exact: true })).toBeVisible();
  await expect(local.getByText(/next background check/)).toBeVisible();
  await expect(page.getByLabel("Default judgment model")).toHaveValue("cloud");
  await page.getByLabel("Default judgment model").selectOption("local");
  await expect(page.getByLabel("Default judgment model")).toHaveValue("local");
  await page.screenshot({ path: info.outputPath("judgment-offline.png"), animations: "disabled" });
  await local.getByRole("button", { name: "Test", exact: true }).first().click();
  dialog = page.getByRole("dialog", { name: "Test connection & speed · local" });
  await dialog.getByRole("button", { name: "Test connection & speed", exact: true }).click();
  await expect(dialog.getByText("40ms", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(local.getByText("Available", { exact: true })).toBeVisible();
  expect(testedIds).toEqual(["local", "local"]);
  const cloud = page.getByRole("article", { name: "Cloud decision" });
  await cloud.getByRole("button", { name: "Edit Cloud decision", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Edit judgment provider" });
  await expect(dialog.getByLabel("API key (optional)", { exact: true })).toHaveValue("********");
  await dialog.getByLabel("Provider name", { exact: true }).fill("Renamed cloud");
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(catalog.providers.cloud?.api_key).toBe("********");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const audit = await new AxeBuilder({ page }).include('section[aria-label="Provider connection"]').withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(audit.violations).toEqual([]);
  await local.getByRole("button", { name: "Delete Local workstation", exact: true }).click();
  await page.getByRole("dialog", { name: "Delete judgment provider", exact: true }).getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(local).not.toBeVisible();
  await expect(page.getByLabel("Default judgment model")).toHaveValue("");
  await expect(page.getByRole("article", { name: "Renamed cloud" })).toBeVisible();
});
