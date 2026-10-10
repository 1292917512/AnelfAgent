import { test, expect } from "../../../../web/frontend/e2e/fixtures";

test("workspace and overview share a confirmed restart without discarding drafts", async ({ page, isMobile }, testInfo) => {
  let restarted = false;
  let requests = 0;
  await page.route("**/api/entity/devops/restart", (route) => { requests++; return route.fulfill({ json: { ok: true, runtime_id: "old" } }); });
  await page.route("**/api/entity/devops/build-state", (route) => route.fulfill({ json: { runtime_id: restarted ? "new" : "old", restarting: !restarted, building: false } }));
  await page.goto("/webui/");
  await page.getByRole("textbox", { name: "Message" }).fill("Preserve this draft");
  await page.getByRole("button", { name: "Workspace tools", exact: true }).click();
  const tools = page.getByRole("dialog", { name: "Workspace tools", exact: true });
  await expect(tools.getByRole("button", { name: "Update, Build & Restart", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("workspace-tools.png") });
  await tools.getByRole("button", { name: "Restart Service", exact: true }).click();
  await page.getByRole("dialog", { name: "Restart Service", exact: true }).getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(tools.getByRole("button", { name: "Restart Service", exact: true })).toBeDisabled();
  await tools.getByRole("button", { name: "Close", exact: true }).click();
  if (isMobile) await page.getByRole("button", { name: "More", exact: true }).click();
  else await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  await page.getByRole("navigation").first().getByRole("link", { name: /Overview/ }).click();
  await expect(page.getByRole("button", { name: "Restart Service", exact: true })).toBeDisabled();
  restarted = true;
  await expect(page.getByRole("button", { name: "Reload page", exact: true })).toBeVisible({ timeout: 10000 });
  expect(requests).toBe(1);
  await page.goBack();
  await expect(page.getByRole("textbox", { name: "Message" })).toHaveValue("Preserve this draft");
});

test("build success followed by refused restart displays the actual failure", async ({ page }) => {
  await page.route("**/api/entity/devops/update-restart", (route) => route.fulfill({ json: { ok: true, runtime_id: "old", operation_id: "op" } }));
  await page.route("**/api/entity/devops/build-state", (route) => route.fulfill({ json: {
    runtime_id: "old", operation_id: "op", building: false, restarting: false,
    last: { ok: true }, result: { ok: false, message: "No supervisor is running" },
  } }));
  await page.goto("/webui/dashboard");
  await page.getByRole("button", { name: "Update, Build & Restart", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "No supervisor is running" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reload page", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
