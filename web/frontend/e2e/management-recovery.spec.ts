import { expect, installEventStream, test } from "./fixtures";
import type { HeartbeatConfig, HeartbeatUpdate, SubAgentProfile } from "../src/lib/types";

test("connection badge distinguishes network, runtime and live stream recovery", async ({ page, context }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await installEventStream(page);
  let ready = true;
  await page.route("**/api/status/", route => route.fulfill({ json: { ready } }));
  await page.goto("/webui/tasks");
  const badge = page.getByRole("button", { name: /^Connection status ·/ });
  await expect(badge).toHaveText("Connected");
  await page.evaluate(() => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "test-disconnect" } })));
  await expect(badge).toHaveText("Reconnecting");
  await badge.click();
  const detail = page.getByRole("dialog", { name: "Connection status" });
  await expect(detail.getByText("Reachable", { exact: true })).toBeVisible();
  await expect(detail.getByText("Reconnecting", { exact: true })).toBeVisible();
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
  await expect(badge).toHaveText("Connected");
  ready = false;
  await detail.getByRole("button", { name: "Check connection" }).click();
  await expect(badge).toHaveText("Not ready");
  await context.setOffline(true);
  await expect(badge).toHaveText("Offline");
  await expect(detail.getByText("Unknown", { exact: true })).toBeVisible();
  ready = true;
  await context.setOffline(false);
  await detail.getByRole("button", { name: "Check connection" }).click();
  await expect(badge).toHaveText("Connected");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("heartbeat conflict keeps the draft until latest schedules are explicitly loaded", async ({ page }) => {
  const config: HeartbeatConfig = { enabled: true, interval_seconds: 600, analysis_temperature: 0.7,
    min_conversations_for_analysis: 3, task_schedules: [{ task_name: "daily-review", mode: "heartbeat", every_n_beats: 10, beat_count: 2 }] };
  const requests: HeartbeatUpdate[] = [];
  await page.route("**/api/config/heartbeat", (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: config });
    const update: HeartbeatUpdate = route.request().postDataJSON();
    requests.push(update);
    if (requests.length === 1) {
      config.task_schedules[0] = { ...config.task_schedules[0]!, every_n_beats: 15, beat_count: 3 };
      return route.fulfill({ status: 409, json: { detail: "Conflict: daily-review" } });
    }
    return route.fulfill({ json: { status: "ok", config } });
  });
  await page.goto("/webui/heartbeat?tab=config");
  const beats = page.getByRole("spinbutton", { name: "beats", exact: true });
  const temperature = page.getByRole("spinbutton", { name: "Analysis Temperature" });
  const save = page.getByRole("button", { name: "Save Config" });
  await beats.fill("13");
  await temperature.fill("0");
  await save.click();
  await expect(page.getByRole("alert")).toHaveText("Conflict: daily-review");
  await expect(beats).toHaveValue("13");
  await expect(save).toBeDisabled();
  expect(requests[0]?.base_task_schedules).toEqual([{ task_name: "daily-review", mode: "heartbeat", every_n_beats: 10 }]);
  await page.getByRole("button", { name: "Reload schedules" }).click();
  await expect(beats).toHaveValue("15");
  await expect(temperature).toHaveValue("0");
  await save.click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual({ analysis_temperature: 0 });
});

test("custom agent forms validate fields, retain failures and submit a retry once", async ({ page }) => {
  const profiles: SubAgentProfile[] = [];
  let creates = 0;
  let saves = 0;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/models/priorities", route => route.fulfill({ json: { chat: [{
    id: "model-a", model: "a", provider_name: "Local", enabled: true,
  }] } }));
  await page.route("**/api/models/sub-agents", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { sub_agents: profiles } });
    if (++creates === 1) return route.fulfill({ status: 503, json: { detail: "Create temporarily unavailable" } });
    profiles.push({ name: "helper", description: "", models: ["model-a"], instructions: "", tool_tags: [], blocked_tools: [],
      tier: 0, builtin: false, output_schema: null, first_available: "model-a", model_missing: false, model_enabled: true });
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/models/sub-agents/helper", async route => {
    if (++saves === 1) return route.fulfill({ status: 503, json: { detail: "Disk unavailable" } });
    Object.assign(profiles[0]!, route.request().postDataJSON());
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/webui/models?tab=subagents");
  const create = page.getByRole("form", { name: "Create profile" });
  const name = create.getByRole("textbox", { name: /Name, e.g./ });
  await name.fill("bad name");
  await name.press("Enter");
  await expect(name).toBeFocused();
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await name.fill("easy");
  await name.press("Enter");
  await expect(create.getByText(/already in use/)).toBeVisible();
  await name.fill("helper");
  await name.press("Enter");
  const model = create.getByRole("button", { name: "Select model" });
  await expect(model).toBeFocused();
  await model.click();
  await page.getByRole("option", { name: /model-a/ }).click();
  await create.getByRole("button", { name: "Create", exact: true }).click();
  await expect(create.getByRole("alert")).toHaveText("Create temporarily unavailable");
  await expect(name).toHaveValue("helper");
  await create.getByRole("button", { name: "Retry" }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const edit = page.getByRole("form", { name: "Edit helper" });
  await edit.getByRole("button", { name: "Facets" }).click();
  const schema = edit.getByRole("textbox", { name: /Output schema JSON/ });
  await schema.fill("[]");
  await edit.getByRole("button", { name: "Facets" }).click();
  await edit.getByRole("button", { name: "Save", exact: true }).click();
  await expect(schema).toBeFocused();
  await expect(schema).toHaveAttribute("aria-invalid", "true");
  expect(saves).toBe(0);
  await schema.fill('{"ok":true}');
  await edit.evaluate(form => { if (form instanceof HTMLFormElement) { form.requestSubmit(); form.requestSubmit(); } });
  await expect(edit.getByRole("alert")).toHaveText("Disk unavailable");
  await expect(schema).toHaveValue('{"ok":true}');
  await edit.getByRole("button", { name: "Retry" }).click();
  await expect(edit).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeFocused();
  expect(saves).toBe(2);
  expect(creates).toBe(2);
  expect(errors).toEqual([]);
});

test("built-in model pool keeps the selected model when its write must be retried", async ({ page }) => {
  const profile: SubAgentProfile = { name: "easy", tier: 1, builtin: true, models: [], description: "", instructions: "",
    tool_tags: [], blocked_tools: [], output_schema: null, first_available: null, model_missing: false, model_enabled: false };
  let writes = 0;
  await page.route("**/api/models/sub-agents", route => route.fulfill({ json: { sub_agents: [profile] } }));
  await page.route("**/api/models/priorities", route => route.fulfill({ json: { chat: [{ id: "chat", enabled: true }] } }));
  await page.route("**/api/models/sub-agents/easy", route => {
    if (++writes === 1) return route.fulfill({ status: 503, json: { detail: "Write failed" } });
    profile.models = route.request().postDataJSON().models;
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/webui/models?tab=subagents");
  const picker = page.getByRole("combobox", { name: "Select model" });
  await picker.selectOption("chat");
  await page.getByTitle("Add to pool").click();
  await expect(page.getByRole("alert")).toHaveText("Write failed");
  await expect(picker).toHaveValue("chat");
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(picker).toHaveValue("");
  await expect(page.getByText("chat", { exact: true })).toBeVisible();
  expect(writes).toBe(2);
});
