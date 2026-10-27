import { test, expect } from "../../../../web/frontend/e2e/fixtures";

test("desktop takeover preserves partial receipts and requires explicit release", async ({ page }) => {
  const control = { state: "running", human: false, revision: 1, automatic_takeover: true,
    active: { operation_id: "action-1", scope: "Desktop task", run_id: "run-1", completed: 2, total: 10 } as object | null };
  const history: object[] = [];
  const submitted: object[] = [];
  await page.route("**/api/entities/operation", route => route.fulfill({ json: {
    name: "operation", group: "operation", type: "tool", source: "entity", enabled: true,
    description: "Desktop operations", tools: [], apis: [], providers: [], config_group: "entity/operation",
    manifest: { display_name: "Operations", description: "Desktop operations" },
  } }));
  await page.route("**/api/entity/operation/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/list")) return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/control")) {
      const request = route.request().postDataJSON();
      submitted.push(request);
      expect(request.revision).toBe(control.revision);
      if (request.action === "takeover") {
        control.state = "human"; control.human = true; control.active = null;
        history.push({ op: "desktop.type", kind: "desktop", args: "", ok: false, detail: "Stopped after partial input", ts: 1,
          receipt: { status: "cancelled", completed: 2, total: 10, process_stopped: true, input_released: true, verification: "skipped" } });
      } else { control.state = "idle"; control.human = false; }
      control.revision++;
      return route.fulfill({ json: { ok: true, control } });
    }
    return route.fulfill({ json: { desktop: { available: true, screen: [1920, 1080], verify: true, hint: "", control },
      active: { context_injecting: true, seconds_since_activity: 0, window_seconds: 600 },
      counts: { mcp_registered: 0 }, history } });
  });
  await page.goto("/webui/entities/operation");
  await page.getByRole("button", { name: "Take over", exact: true }).click();
  await expect(page.getByText("User in control", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Stop action" })).toBeDisabled();
  await page.getByText("desktop.type · Stopped", { exact: true }).click();
  await expect(page.getByText("2 / 10", { exact: true })).toBeVisible();
  await expect(page.getByText("Worker exited", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Return to AI", exact: true }).click();
  await expect(page.getByText("Idle", { exact: true })).toBeVisible();
  expect(submitted).toEqual([{ action: "takeover", revision: 1 }, { action: "release", revision: 2 }]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});
