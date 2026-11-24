import { test, expect, task } from "./fixtures";
import type { HeartbeatConfig } from "../src/lib/types";

test("heartbeat preserves a zero-temperature draft, retries and saves only the edited field", async ({ page }) => {
  const config: HeartbeatConfig = { enabled: true, interval_seconds: 600, analysis_temperature: 0.7, min_conversations_for_analysis: 3, task_schedules: [] };
  const writes: Partial<HeartbeatConfig>[] = [];
  await page.route("**/api/config/heartbeat", (route) => {
    if (route.request().method() !== "PUT") return route.fulfill({ json: config });
    const patch: Partial<HeartbeatConfig> = route.request().postDataJSON();
    writes.push(patch);
    if (writes.length === 1) return route.fulfill({ status: 503, json: { detail: "Save offline" } });
    Object.assign(config, patch);
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/webui/heartbeat?tab=config");
  const temperature = page.getByRole("spinbutton", { name: "Analysis Temperature" });
  const save = page.getByRole("button", { name: "Save Config" });
  await temperature.fill("");
  await save.click();
  expect(writes).toHaveLength(0);
  await temperature.fill("0");
  await page.getByRole("tab").first().click();
  await page.getByRole("button", { name: "Keep editing" }).click();
  await expect(temperature).toHaveValue("0");
  await save.click();
  await expect(page.getByRole("alert")).toHaveText("Save offline");
  await expect(temperature).toHaveValue("0");
  await save.click();
  await expect(save).toBeDisabled();
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  expect(writes).toEqual([{ analysis_temperature: 0 }, { analysis_temperature: 0 }]);
});

test("task history keeps uncertain progress distinct from successful work", async ({ page }) => {
  const record = { started_at: 1720000000, duration_ms: 4200, status: "incomplete", trigger: "scheduled",
    preview: "File written, verification still pending", error: "Execution budget exhausted" };
  await page.route("**/api/config/tasks", route => route.fulfill({ json: [{ ...task, last_run: record }] }));
  await page.route("**/api/config/tasks/daily-review/history", route => route.fulfill({ json: [record] }));
  await page.goto("/webui/tasks");
  await expect(page.getByText("Incomplete, verify before continuing", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Daily review/ }).click();
  await expect(page.getByText("Execution History", { exact: true })).toBeVisible();
  await expect(page.getByText(record.preview, { exact: true })).toBeVisible();
  await expect(page.getByText(record.error, { exact: true })).toBeVisible();
  await expect(page.getByText("Incomplete, verify before continuing", { exact: true })).toHaveCount(2);
});

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
