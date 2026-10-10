import { test, expect } from "./fixtures";

test("heartbeat reads the canonical memory path and recovers a local log error", async ({ page }) => {
  await page.route("**/api/status/", (route) => route.fulfill({ json: {
    ready: true, status: { status: "running", uptime: 3661.4, mind_phase: "idle" },
  } }));
  await page.route("**/api/config/heartbeat/status", (route) => route.fulfill({ json: {
    enabled: true, total_ticks: 4, interval_seconds: 300, schedules: [],
  } }));
  let failed = true;
  const paths: string[] = [];
  await page.route("**/api/memory/files/content*", (route) => {
    paths.push(new URL(route.request().url()).searchParams.get("path") ?? "");
    return route.fulfill(failed
      ? { status: 500, json: { detail: "Log temporarily unavailable" } }
      : { json: { content: "### Heartbeat recovered\n- Completed read-only inspection" } });
  });
  await page.goto("/webui/heartbeat");
  await expect(page.getByText("Log temporarily unavailable")).toBeVisible();
  await expect(page.getByRole("button", { name: "Manual Tick" })).toBeEnabled();
  await expect(page.getByText("1h 1m", { exact: true })).toBeVisible();
  failed = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByText("Completed read-only inspection", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("button", { name: "Refresh", exact: true })).toBeEnabled();
  expect(paths.length).toBeGreaterThanOrEqual(2);
  expect(paths.every((path) => path === "memory/heartbeat.md")).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("heartbeat does not offer execution before the runtime is ready", async ({ page }) => {
  await page.route("**/api/status/", (route) => route.fulfill({ json: { ready: false } }));
  await page.route("**/api/config/heartbeat/status", (route) => route.fulfill({ json: {
    enabled: false, total_ticks: 0, schedules: [],
  } }));
  await page.goto("/webui/heartbeat");
  await expect(page.getByRole("button", { name: "Manual Tick" })).toBeDisabled();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
  await expect(page.getByText("Research notes", { exact: true })).toBeVisible();
});
