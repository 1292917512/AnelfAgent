import { test, expect } from "../../../../web/frontend/e2e/fixtures";
import type { DesktopModuleInfo, SubscriptionProvider } from "../types";

test("subscription login, refresh and failure states remain readable", async ({ page }, info) => {
  let enabled = true;
  let loginState = "waiting";
  let images = 0;
  let starts = 0;
  let credentialReads = 0;
  let provider: SubscriptionProvider = {
    key: "bailian", name: "阿里百炼", source: "Bailian CLI", auth_type: "console", state: "no_credential",
  };
  const module: DesktopModuleInfo = {
    key: "subscription", display_name: "订阅额度", description: "订阅用量与账户余额监控",
    priority: 30, enabled: true, default_enabled: true, refresh_interval: 1800,
    last_refresh: null, last_error: "", configs: [], current_content: null, detail: {},
  };
  await page.route("**/api/provider-keys", (route) => route.fulfill({ json: { providers: [{
    name: "bailian_console", title: "百炼控制台授权", description: "Console authorization for quota queries",
    domain: "subscription", domains: ["subscription"], owner: "entity:ai_desktop", destination: "/entities/ai_desktop",
    editor: "bailian-console", configured: null, fields: [],
  }] } }));
  await page.route("**/api/entities/ai_desktop", (route) => route.fulfill({ json: {
    name: "ai_desktop", group: "ai_desktop", type: "tool", source: "entity", enabled: true,
    manifest: { display_name: "AI Desktop" }, description: "", config_group: "entity/ai_desktop",
    config_items: [], configs: {}, tools: [], providers: [], apis: [],
  } }));
  await page.route("**/api/entity/ai_desktop/modules", (route) => route.fulfill({ json: {
    modules: [{ ...module, configs: [{ key: "ai_desktop_subscription_provider_bailian_enabled",
      name: "provider_bailian_enabled", description: "Monitor Bailian quota", value: enabled, default: false,
      value_type: "boolean", unit: "", min: null, max: null, advanced: false }],
    detail: { providers: enabled ? [provider] : [] } }], count: 1,
  } }));
  await page.route("**/api/entities/ai_desktop/config", async (route) => {
    enabled = route.request().postDataJSON().value as boolean;
    await route.fulfill({ json: { success: true } });
  });
  await page.route("**/api/entity/ai_desktop/modules/subscription/login**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/login") && route.request().method() === "GET") {
      credentialReads++;
      await route.fulfill({ json: { available: true, configured: loginState === "complete", monitoring: enabled } });
      return;
    }
    if (path.endsWith("/image")) {
      images++;
      await route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="168" height="168"><rect width="168" height="168" fill="white"/><path d="M10 10h50v50H10zM108 10h50v50h-50zM10 108h50v50H10zM86 80h60v60H86z" fill="black"/></svg>' });
      return;
    }
    if (path.endsWith("/confirm")) {
      loginState = "complete";
      provider = { ...provider, state: "ok", plan: "Token Plan", windows: [{ label: "每月", remaining_percent: 75 }] };
    }
    if (path.endsWith("/login") && route.request().method() === "POST") {
      starts++;
    }
    await route.fulfill({ json: { id: "session", state: loginState, error: "", expires_at: 0 } });
  });
  await page.route("**/api/entity/ai_desktop/preview", (route) => route.fulfill({ json: { injecting: false, content: "" } }));
  await page.route("**/api/entity/ai_desktop/modules/subscription/refresh", async (route) => {
    provider = { ...provider, state: "ok", plan: "Token Plan", fetched_at: 1791792000,
      windows: [{ label: "每月", remaining_percent: 75, reset_at: 1793462400 }] };
    await route.fulfill({ json: { success: true, last_error: null, content: "" } });
  });
  await page.goto("/webui/entities/ai_desktop");
  await page.getByRole("link", { name: "Manage credentials" }).click();
  const card = page.getByRole("region", { name: "百炼控制台授权", exact: true });
  await expect(card.getByText("Custom authorization")).toBeVisible();
  expect(credentialReads).toBe(0);
  expect(starts).toBe(0);
  await card.getByRole("button").click();
  await page.getByRole("button", { name: "Sign in with QR code" }).click();
  const dialog = page.getByRole("dialog", { name: "Sign in with QR code", exact: true });
  const loginImage = dialog.getByRole("img", { name: "Aliyun sign-in QR code", exact: true });
  await expect(loginImage).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close", exact: true }).last()).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: info.outputPath("subscription-login.png"), animations: "disabled" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  loginState = "scanned";
  await expect(dialog.getByText("Code scanned. Confirm sign-in on your phone.")).toBeVisible();
  expect(images).toBe(1);
  loginState = "confirming";
  await dialog.getByRole("button", { name: "Confirm authorization", exact: true }).click();
  await expect(dialog.getByText("Signed in. Authorization will be reused.")).toBeVisible();
  expect(starts).toBe(1);
  await dialog.getByRole("button", { name: "Close", exact: true }).last().click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("Authorization saved", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Open owning feature" }).click();
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
  await page.getByRole("switch", { name: "Monitor Bailian quota" }).click();
  await expect(quota).toHaveCount(0);
});
