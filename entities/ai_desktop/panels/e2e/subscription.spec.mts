import { test, expect } from "../../../../web/frontend/e2e/fixtures";
import type { DesktopModuleInfo, SubscriptionProvider } from "../types";

test("subscription login, refresh and failure states remain readable", async ({ page }, info) => {
  let provider: SubscriptionProvider = {
    key: "bailian", name: "阿里百炼", source: "Bailian CLI", auth_type: "console", state: "no_credential",
  };
  const module: DesktopModuleInfo = {
    key: "subscription", display_name: "订阅额度", description: "订阅用量与账户余额监控",
    priority: 30, enabled: true, default_enabled: true, refresh_interval: 1800,
    last_refresh: null, last_error: "", configs: [], current_content: null, detail: {},
  };
  await page.route("**/api/entities/ai_desktop", (route) => route.fulfill({ json: {
    name: "ai_desktop", group: "ai_desktop", type: "tool", source: "entity", enabled: true,
    manifest: { display_name: "AI Desktop" }, description: "", config_group: "entity/ai_desktop",
    config_items: [], configs: {}, tools: [], providers: [], apis: [],
  } }));
  await page.route("**/api/entity/ai_desktop/modules", (route) => route.fulfill({ json: {
    modules: [{ ...module, detail: { providers: [provider] } }], count: 1,
  } }));
  await page.route("**/api/entity/ai_desktop/preview", (route) => route.fulfill({ json: { injecting: false, content: "" } }));
  await page.route("**/api/entity/ai_desktop/modules/subscription/refresh", async (route) => {
    provider = { ...provider, state: "ok", plan: "Token Plan", fetched_at: 1791792000,
      windows: [{ label: "每月", remaining_percent: 75, reset_at: 1793462400 }] };
    await route.fulfill({ json: { success: true, last_error: null, content: "" } });
  });
  await page.goto("/webui/entities/ai_desktop");
  await expect(page.getByText("bl auth login --console")).toBeVisible();
  await page.getByRole("button", { name: "Refresh data now" }).click();
  const quota = page.getByRole("region", { name: "阿里百炼" });
  await expect(quota.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "75");
  await expect(quota.getByText("Remaining 75%")).toBeVisible();
  await quota.scrollIntoViewIfNeeded();
  await page.screenshot({ path: info.outputPath("subscription.png") });
  expect(await quota.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  await page.route("**/api/entity/ai_desktop/modules/subscription/refresh", async (route) => {
    provider = { ...provider, state: "error", plan: null, windows: [], error: "Quota query timed out" };
    await route.fulfill({ json: { success: true, last_error: null, content: "" } });
  });
  await page.getByRole("button", { name: "Refresh data now" }).click();
  await expect(quota.getByText("Quota query timed out")).toBeVisible();
  await expect(quota.getByRole("progressbar")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh data now" })).toBeEnabled();
});
