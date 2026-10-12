import { test, expect } from "../../../../web/frontend/e2e/fixtures";
import type { DeviceInfo, DomainActionInfo } from "../types";

const action = (description: string, values: Partial<DomainActionInfo> = {}): DomainActionInfo => ({
  description, accepts_value: false, value_hint: "", min: null, max: null, step: null, options: [], ...values,
});
const power = { turn_on: action("On"), turn_off: action("Off") };
const device = (domain: string, name: string, state: string, area: string, extra: Partial<DeviceInfo> = {}): DeviceInfo => ({
  entity_id: domain + "." + name.toLowerCase().replace(/ /g, "_"), domain, name, state, area, provider: "ha",
  available: true, stale: false, updated_at: 1791777600, attributes: {}, actions: power, ...extra,
});
const devices = [
  device("light", "Pendant light", "on", "Living room", { attributes: { brightness: 180, color_temp_kelvin: 3500 }, actions: { ...power, set_brightness: action("Brightness", { min: 0, max: 100, step: 1 }), set_color_temp: action("Temperature", { min: 2700, max: 6500, step: 100 }) } }),
  device("climate", "Air conditioner", "cool", "Living room", { attributes: { temperature: 24, current_temperature: 26, temperature_unit: "°C" }, actions: { ...power, set_temperature: action("Temperature", { min: 16, max: 30, step: 0.5 }), set_hvac_mode: action("Mode", { options: ["off", "cool", "heat"] }) } }),
  device("media_player", "Home speaker", "playing", "Living room", { provider: "xiaomi", attributes: { volume_level: 0.35, media_title: "Quiet afternoon" }, actions: { media_play: action("Play"), media_pause: action("Pause"), speak: action("Speak"), set_volume: action("Volume", { min: 0, max: 100, step: 1 }) } }),
  device("cover", "Bedroom curtains", "open", "Bedroom", { attributes: { current_position: 75 }, actions: { open_cover: action("Open"), close_cover: action("Close"), stop_cover: action("Stop"), set_position: action("Position", { min: 0, max: 100, step: 1 }) } }),
  device("light", "Bedside light", "off", "Bedroom", { available: false, stale: true }),
  device("sensor", "Room temperature", "23.5", "Bedroom", { attributes: { unit_of_measurement: "°C" }, actions: {} }),
];
const domains = ["light", "climate", "media_player", "cover", "sensor", "scene", "switch"].map((key, priority) => ({
  key, display_name: key, description: "", priority, ha_domains: [key], enabled: true, default_enabled: true,
  device_count: devices.filter((d) => d.domain === key).length, available_count: devices.filter((d) => d.domain === key && d.available).length, actions: {}, configs: [],
}));
const status = { configured: true, connected: true, device_count: devices.length, providers: [
  { provider: "ha", provider_name: "Home Assistant", configured: true, connected: true, device_count: 5, last_error: null, connected_at: 1791777600 },
  { provider: "xiaomi", provider_name: "Xiaomi", configured: true, connected: true, device_count: 1, last_error: null, connected_at: 1791777600 },
  { provider: "xiaodu", provider_name: "Xiaodu", configured: false, connected: false, device_count: 0, last_error: null, connected_at: null },
] };
const item = (key: string, description: string, value: unknown, type = "string", enum_options: string[] | null = null) => ({ key, description, value, type, enum_options, default: value, editable: true, required: false });
const config = { config_group: "entity/smart_home", items: [
  item("smart_home_ha_url", "Home Assistant URL", "http://homeassistant.local:8123"),
  item("smart_home_ha_token", "Access token", "******", "password"),
  item("smart_home_context_inject", "AI awareness", true, "boolean"),
  item("smart_home_context_mode", "Awareness policy", "on_demand", "enum", ["on_demand", "always"]),
] };

test.beforeEach(async ({ page }) => {
  await page.route("**/api/entities/smart_home", (route) => route.fulfill({ json: {
    name: "smart_home", group: "smart_home", type: "tool", source: "entities", enabled: true,
    description: "Your connected home", config_group: "entity/smart_home", tools: [], apis: [], providers: [],
    manifest: { display_name: "Smart Home", description: "Your connected home", version: "1.0.0" },
  } }));
  await page.route("**/api/entities/smart_home/config", (route) => route.fulfill({ json: config }));
  await page.route("**/api/entity/smart_home/**", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop();
    if (path === "stream") return route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" });
    const responses: Record<string, unknown> = { status, devices: { devices, count: devices.length, connection: status },
      domains: { domains }, preview: { injecting: true, activation_required: true, content: "[智能家居]\nLiving room:\n- 灯光: Pendant light: 开 · 亮度 71%" } };
    return route.fulfill({ json: responses[path ?? ""] ?? {} });
  });
});

test("rooms, controls, stale states and command failures", async ({ page }, testInfo) => {
  let calls = 0;
  await page.route("**/api/entity/smart_home/control", async (route) => {
    calls += 1;
    await route.fulfill({ status: 502, json: { detail: "Device did not accept the command" } });
  });
  await page.goto("/webui/entities/smart_home");
  await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(6);
  await expect(page.getByRole("article", { name: "Bedside light" }).getByRole("button")).toBeDisabled();
  const speaker = page.getByRole("article", { name: "Home speaker" });
  const text = speaker.getByRole("textbox");
  await text.fill("Dinner is ready");
  await text.press("Enter");
  await expect(speaker.getByRole("alert")).toHaveText("Device did not accept the command");
  await expect(text).toHaveValue("Dinner is ready");
  expect(calls).toBe(1);
  await page.getByLabel("Rooms", { exact: true }).getByRole("button", { name: /^Living room 3$/ }).click();
  await expect(page.getByRole("article")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.getByRole("article", { name: "Air conditioner" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("devices-light.png"), fullPage: true });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  await expect(page.getByRole("article").first()).toHaveCSS("background-color", "rgb(23, 30, 43)");
  await page.screenshot({ path: testInfo.outputPath("devices-dark.png"), fullPage: true, animations: "disabled" });
});

test("connection settings save a batch without sending masked secrets", async ({ page }, testInfo) => {
  let saved: unknown;
  await page.route("**/api/entities/smart_home/config/batch", async (route) => {
    saved = route.request().postDataJSON();
    await route.fulfill({ json: { updated: 1 } });
  });
  await page.goto("/webui/entities/smart_home");
  await page.getByRole("tab", { name: "Connections", exact: true }).click();
  await page.getByRole("button", { name: "Connection settings" }).first().click();
  const url = page.getByLabel("Home Assistant URL", { exact: true });
  await url.fill("http://home.local:8123");
  await expect(page.getByLabel("Access token", { exact: true })).toHaveValue("");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect.poll(() => saved).toEqual({ updates: { smart_home_ha_url: "http://home.local:8123" } });
  await page.screenshot({ path: testInfo.outputPath("connections.png"), fullPage: true });
});

test("awareness makes activation policy explicit", async ({ page }, testInfo) => {
  await page.goto("/webui/entities/smart_home");
  await page.getByRole("tab", { name: "AI awareness", exact: true }).click();
  await expect(page.getByLabel("Awareness policy")).toHaveValue("on_demand");
  await expect(page.getByText(/Preview after smart_home is activated/)).toBeVisible();
  await expect(page.getByText(/Pendant light: 开/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("awareness.png"), fullPage: true });
});

test("Chinese interface and compact phone layout", async ({ page }, testInfo) => {
  await page.addInitScript(() => localStorage.setItem("i18nextLng", "zh"));
  await page.goto("/webui/entities/smart_home");
  await expect(page.getByRole("heading", { name: "我的家" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "AI 感知" })).toBeVisible();
  await page.getByLabel("房间", { exact: true }).getByRole("button", { name: /^Living room 3$/ }).click();
  await page.getByRole("article", { name: "Air conditioner" }).scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("devices-zh.png"), fullPage: true, animations: "disabled" });
});

test("only discovered types and configured platforms appear by default", async ({ page }) => {
  await page.goto("/webui/entities/smart_home");
  await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Xiaodu", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Scenes", exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "AI awareness", exact: true }).click();
  await expect(page.getByRole("switch", { name: "Lighting", exact: true })).toBeVisible();
  await expect(page.getByRole("switch", { name: "Scenes", exact: true })).toHaveCount(0);
  await expect(page.getByRole("switch", { name: "Switches", exact: true })).toHaveCount(0);
  await page.getByRole("tab", { name: "Connections", exact: true }).click();
  await expect(page.getByText("Xiaodu", { exact: true })).not.toBeVisible();
  await page.getByText("Add device platform", { exact: true }).click();
  await expect(page.getByText("Xiaodu", { exact: true })).toBeVisible();
});

test("an empty home has an add-platform path without empty device sections", async ({ page }) => {
  const emptyStatus = { configured: false, connected: false, device_count: 0, providers: status.providers.map((provider) => ({ ...provider, configured: false, connected: false, device_count: 0 })) };
  await page.route("**/api/entity/smart_home/status", (route) => route.fulfill({ json: emptyStatus }));
  await page.route("**/api/entity/smart_home/devices", (route) => route.fulfill({ json: { devices: [], count: 0, connection: emptyStatus } }));
  await page.goto("/webui/entities/smart_home");
  await expect(page.getByRole("heading", { name: "My home" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(0);
  await expect(page.getByText("Available devices", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Connect a platform", exact: true }).click();
  await expect(page.getByText("Add device platform", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "AI awareness", exact: true }).click();
  await expect(page.getByText("Awareness settings appear automatically when devices are discovered.")).toBeVisible();
  await expect(page.getByRole("switch", { name: "Lighting", exact: true })).toHaveCount(0);
});
