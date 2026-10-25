import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";

test("channel reload preserves filters and drafts, serializes actions and reports partial failures", async ({ page, isMobile }, info) => {
  if (isMobile) await page.setViewportSize({ width: 360, height: 800 });
  let calls = 0;
  let allCalls = 0;
  let version = 1;
  let release = () => {};
  const summary = { added: [], removed: [], reloaded: ["telegram"], failed: [], total: 2 };
  await page.route("**/api/adapters/", (route) => route.fulfill({ json: { ready: true, adapters: [
    { key: "telegram", name: "Telegram", status: "reconnecting", status_display: "Reconnecting", detail: `Connected ${version}`, capabilities: [] },
    { key: "matrix", name: "Matrix", status: "stopped", status_display: "Stopped", capabilities: [] },
  ] } }));
  await page.route("**/api/adapters/telegram/reload", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 500, json: { detail: "Reload failed; previous channel restored" } });
      return;
    }
    await new Promise<void>((resolve) => { release = resolve; });
    version += 1;
    await route.fulfill({ json: summary });
  });
  await page.route("**/api/adapters/reload", (route) => {
    allCalls += 1;
    return route.fulfill({ json: { ...summary, reloaded: [], failed: ["matrix"] } });
  });
  await page.goto("/webui/channels?channel=telegram");
  const reload = page.getByRole("button", { name: "Reload channel Telegram", exact: true });
  await expect(reload).toBeVisible();
  const field = page.getByLabel("Bot token", { exact: true });
  await field.fill("unsaved-token");
  await expect(reload).toBeDisabled();
  await expect(page.getByRole("button", { name: "Sync all channels", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Reset", exact: true }).click();
  await reload.click();
  await expect(page.getByRole("alert")).toContainText("previous channel restored");
  await expect(page.getByText("Connected 1", { exact: true })).toBeVisible();
  await reload.click();
  await expect(reload).toHaveAttribute("aria-busy", "true");
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Sync all channels", exact: true })).toBeDisabled();
  release();
  await expect(page.getByText("Connected 2", { exact: true })).toBeVisible();
  await expect(reload).toBeEnabled();
  await expect(page.getByRole("searchbox")).toHaveValue("telegram");
  await expect(field).toHaveValue("abcd****5678");
  expect(allCalls).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: info.outputPath("single-channel-reload.png"), animations: "disabled" });
  await page.getByRole("button", { name: "Sync all channels", exact: true }).click();
  await expect(page.getByText("Some channels failed to reload: matrix. Check channel status and server logs.", { exact: true })).toBeVisible();
  expect(calls).toBe(2);
});
