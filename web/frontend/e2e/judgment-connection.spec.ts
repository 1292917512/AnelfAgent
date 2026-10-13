import AxeBuilder from "@axe-core/playwright";
import type { DecisionProvider, JudgmentCatalog } from "../src/lib/types";
import { expect, test } from "./fixtures";

test("judgment providers support local models, isolated health and complete editing", async ({ page }, info) => {
  const catalog: JudgmentCatalog = {
    providers: { cloud: { name: "Cloud decision", base_url: "https://api.typesafe.ai", api_key: "********", models: { cloud: { name: "Cloud Jev", model: "jev-latest" } } } },
    default_model: "cloud", fallback_models: [], revision: "0", health: {},
  };
  let revision = 0;
  let testCalls = 0;
  const testedIds: string[] = [];
  const inferenceModes: boolean[] = [];
  await page.route("**/api/judgment/providers", (route) => route.fulfill({ json: catalog }));
  await page.route("**/api/judgment/providers/*", (route) => {
    const id = route.request().url().split("/").pop()!;
    if (route.request().method() === "DELETE") {
      delete catalog.providers[id];
      const remaining = [catalog.default_model, ...catalog.fallback_models].filter((mid) => Object.values(catalog.providers).some((provider) => mid in provider.models));
      catalog.default_model = remaining[0] || "";
      catalog.fallback_models = remaining.slice(1);
    } else {
      const provider: DecisionProvider = route.request().postDataJSON();
      catalog.providers[id] = { ...provider, api_key: provider.api_key ? "********" : "" };
    }
    catalog.revision = String(++revision);
    return route.fulfill({ json: catalog });
  });
  await page.route("**/api/judgment/priority", (route) => {
    const order: string[] = route.request().postDataJSON().model_ids;
    catalog.default_model = order[0] || "";
    catalog.fallback_models = order.slice(1);
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
    const inference: boolean = route.request().postDataJSON().inference;
    inferenceModes.push(inference);
    const offline = ++testCalls === 1;
    catalog.health![id] = { status: offline ? "offline" : "ready", checked_at: Date.now() / 1000, retry_at: offline ? Date.now() / 1000 + 300 : 0, error: offline ? "Connection refused" : "", latency_ms: 40 };
    return route.fulfill({ json: { ok: !offline, source: "typesafe", model: "StartLux-Decision-27B", latency_ms: 40,
      error: offline ? "Connection refused" : undefined, cause: offline ? "network" : undefined,
      kind: inference ? "inference" : "connectivity", slow: false, slow_after_ms: 1000, gate_budget_ms: 5000,
      answers: inference ? { needed: { type: "noul", noul: 0.9 } } : undefined } });
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
  await expect(page.getByRole("list", { name: "Model priority" }).getByRole("listitem").first()).toContainText("Cloud Jev");
  await local.getByRole("button", { name: "Test", exact: true }).first().click();
  dialog = page.getByRole("dialog", { name: "Check connectivity · local" });
  await dialog.getByRole("button", { name: "Check connectivity", exact: true }).click();
  await expect(dialog.getByText("Test failed", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(local.getByText("Unavailable · skipped", { exact: true })).toBeVisible();
  await expect(local.getByText(/next background check/)).toBeVisible();
  await expect(page.getByRole("list", { name: "Model priority" }).getByRole("listitem").first()).toContainText("Cloud Jev");
  await page.getByLabel("Add candidate model", { exact: true }).selectOption("local");
  await page.getByRole("button", { name: "Move Local decision up", exact: true }).click();
  await expect(page.getByRole("list", { name: "Model priority" }).getByRole("listitem").first()).toContainText("Local decision");
  expect(catalog.fallback_models).toEqual(["cloud"]);
  await page.screenshot({ path: info.outputPath("judgment-offline.png"), animations: "disabled" });
  await local.getByRole("button", { name: "Test", exact: true }).first().click();
  dialog = page.getByRole("dialog", { name: "Check connectivity · local" });
  await dialog.getByRole("button", { name: "Check connectivity", exact: true }).click();
  await expect(dialog.getByText("40ms", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(local.getByText("Available", { exact: true })).toBeVisible();
  expect(testedIds).toEqual(["local", "local"]);
  expect(inferenceModes).toEqual([false, false]);
  await local.getByRole("button", { name: "Test", exact: true }).first().click();
  dialog = page.getByRole("dialog", { name: "Check connectivity · local" });
  await dialog.getByRole("button", { name: "Test inference (uses quota)", exact: true }).click();
  await expect(dialog.getByText('"needed":', { exact: false })).toBeVisible();
  expect(inferenceModes).toEqual([false, false, true]);
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await page.screenshot({ path: info.outputPath("judgment-priority.png"), animations: "disabled" });
  await page.getByRole("list", { name: "Model priority" }).screenshot({ path: info.outputPath("priority-detail.png"), animations: "disabled" });
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
  await expect(page.getByRole("list", { name: "Model priority" }).getByRole("listitem").first()).toContainText("Cloud Jev");
  await expect(page.getByRole("article", { name: "Renamed cloud" })).toBeVisible();
});
