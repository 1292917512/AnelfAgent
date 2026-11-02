import { expect, test, type Page } from "../../../../web/frontend/e2e/fixtures";

const callbackPath = "/api/channels/qqbot/webhook";
const intents = { chats: 1 << 25, mentions: 1 << 30, direct: 1 << 12 };
const customFlag = 1 << 16;

async function mockBot(page: Page, registered: boolean) {
  const state = { status: "stopped", starts: 0, lastEvent: 0, rejectSecret: true, error: "" };
  const values: Record<string, string | number | boolean> = {
    enabled: false, app_id: "", app_secret: "", connection_mode: "webhook",
    intents: intents.chats | intents.mentions | customFlag, require_mention: true, allowed_chats: "",
  };
  const tags: Record<string, string> = { enabled: "startup", app_id: "account", app_secret: "account",
    connection_mode: "transport", intents: "subscriptions" };
  const types: Record<string, string> = { enabled: "boolean", app_secret: "password",
    connection_mode: "enum", intents: "integer", require_mention: "boolean" };
  await page.route("**/api/adapters/", route => route.fulfill({ json: { ready: true,
    adapters: registered || state.status !== "stopped" ? [{ key: "qqbot", name: "QQ Official Bot",
      status: state.status, status_display: state.status }] : [] } }));
  await page.route("**/api/channels/qqbot/setup", route => route.fulfill({ json: { callback_path: callbackPath, intents,
    last_event_at: state.lastEvent, status: state.status, mode: state.status === "stopped" ? null : values.connection_mode,
    bot_name: state.status === "running" ? "Demo bot" : "", self_id: state.status === "running" ? "42" : "", last_error: state.error } }));
  await page.route("**/api/config/meta", route => route.fulfill({ json: { groups: [{ group: "adapter/qqbot",
    items: Object.entries(values).map(([key, value]) => ({ key: "qqbot_" + key, value, default: value, editable: true,
      type: types[key] ?? "string", tag: tags[key] ?? "", options: key === "connection_mode" ? ["webhook", "websocket"] : [] })) }] } }));
  await page.route("**/api/config/meta/channels/qqbot", route => {
    const patch = route.request().postDataJSON() as typeof values;
    if ("qqbot_app_secret" in patch && state.rejectSecret) return route.fulfill({ status: 409, json: { detail: "Secret could not be saved" } });
    const persisted = { ...patch };
    for (const key of Object.keys(patch)) {
      if (key === "qqbot_app_secret") persisted[key] = "****";
      values[key.slice("qqbot_".length)] = persisted[key]!;
    }
    return route.fulfill({ json: { values: persisted } });
  });
  await page.route("**/api/adapters/qqbot/start", route => {
    expect(route.request().method()).toBe("PUT");
    state.starts++;
    state.status = "running";
    values.enabled = true;
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/adapters/qqbot/test/health", route => route.fulfill({ json: { running: true, healthy: true } }));
  return { state, values };
}

test("guided setup saves before connecting, keeps failed drafts and verifies inbound events separately", async ({ page, isMobile }) => {
  if (isMobile) await page.setViewportSize({ width: 360, height: 800 });
  const { state, values } = await mockBot(page, true);
  await page.goto("/webui/channels");
  await page.getByRole("button", { name: "Set up connection" }).click();
  await expect(page.getByRole("button", { name: "Connect bot" })).toBeDisabled();
  await page.getByLabel("Bot AppID", { exact: true }).fill("12345");
  const secret = page.getByLabel("Bot AppSecret", { exact: true });
  await expect(secret).toHaveAttribute("type", "password");
  await secret.fill("temporary-secret");
  await page.getByRole("radio", { name: "WebSocket connection" }).check();
  const mask = page.getByRole("spinbutton", { name: "WebSocket event mask" });
  const direct = page.getByRole("checkbox", { name: "Guild direct messages" });
  const apply = page.getByRole("button", { name: "Save and connect" });
  await mask.fill("0");
  await expect(apply).toBeDisabled();
  await mask.fill(String(intents.chats | intents.mentions | customFlag));
  await direct.check();
  await expect(mask).toHaveValue(String(intents.chats | intents.mentions | intents.direct | customFlag));
  await expect(page.getByLabel("Public HTTPS address")).not.toBeVisible();
  await apply.click();
  await expect(page.getByRole("region", { name: "Connect a QQ official bot" }).getByRole("alert")).toHaveText("Secret could not be saved");
  expect(state.starts).toBe(0);
  expect(values.app_id).toBe("");
  await expect(secret).toHaveValue("temporary-secret");
  state.rejectSecret = false;
  await apply.click();
  const check = page.getByRole("button", { name: "Check QQ API" });
  await expect(check).toBeEnabled();
  expect(state.starts).toBe(1);
  expect(values.intents).toBe(intents.chats | intents.mentions | intents.direct | customFlag);
  await expect(secret).toHaveValue("****");
  const overview = page.getByRole("region", { name: "QQ bot connection", exact: true });
  await expect(overview.getByRole("heading", { name: "Demo bot" })).toBeVisible();
  await expect(overview.getByRole("link", { name: "Open QQ Open Platform" })).toHaveAttribute("href", "https://q.qq.com/");
  await expect(overview.getByLabel("Public HTTPS address")).toBeHidden();
  await check.click();
  await expect(page.getByText("QQ API check passed.")).toBeVisible();
  await expect(page.getByText("No events received yet")).toBeVisible();
  state.lastEvent = Date.now() / 1000;
  await expect(page.getByText("No events received yet")).toHaveCount(0, { timeout: 10000 });
  await overview.getByRole("switch", { name: /Require a mention/ }).click();
  const allowList = overview.getByRole("textbox", { name: /Allowed conversation targets/ });
  await allowList.fill("group.G");
  await page.getByRole("switch", { name: "Connect automatically when Anelf starts" }).click();
  await expect(page.getByRole("button", { name: "Save and apply" })).toBeDisabled();
  await page.getByRole("button", { name: "Save settings only" }).click();
  await expect(overview.getByRole("button", { name: "Save settings only" })).toBeHidden();
  await expect(allowList).toHaveValue("group.G");
  expect(values.allowed_chats).toBe("group.G");
  expect(values.require_mention).toBe(false);
  expect(values.enabled).toBe(false);
  expect(state.starts).toBe(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
});

test("Webhook setup works before registration and builds a copyable public URL", async ({ page, isMobile }, info) => {
  if (isMobile) await page.setViewportSize({ width: 360, height: 800 });
  const { state } = await mockBot(page, false);
  await page.addInitScript(() => localStorage.setItem("i18nextLng", "zh"));
  let setupAvailable = false;
  await page.route("**/api/channels/qqbot/setup", route => setupAvailable ? route.fallback()
    : route.fulfill({ status: 404, json: { detail: "Setup endpoint unavailable" } }));
  await page.goto("/webui/channels");
  await page.getByRole("searchbox").fill("官方机器人");
  await page.getByRole("button", { name: "配置与连接" }).click();
  await expect(page.getByLabel("机器人 AppID", { exact: true })).toBeVisible();
  const retry = page.getByRole("button", { name: "重新加载" });
  await expect(retry).toBeVisible();
  setupAvailable = true;
  await retry.click();
  const host = page.getByLabel("公网 HTTPS 地址", { exact: true });
  const output = page.getByLabel("粘贴到 QQ 开放平台的回调地址");
  const copy = page.getByRole("button", { name: "复制回调地址" });
  await expect(host).toHaveValue("");
  await expect(copy).toBeDisabled();
  await expect(page.getByRole("spinbutton", { name: "WebSocket 订阅掩码" })).toBeHidden();
  await host.fill("https://192.168.1.3:443");
  await expect(host).toHaveAttribute("aria-invalid", "true");
  await expect(copy).toBeDisabled();
  await host.fill("bot.example.com:8443/proxy/");
  const full = "https://bot.example.com:8443/proxy" + callbackPath;
  await expect(output).toHaveValue(full);
  await host.fill(full);
  await expect(output).toHaveValue(full);
  await page.getByRole("radio", { name: "WebSocket 长连接" }).check();
  await page.getByRole("radio", { name: "Webhook 回调" }).check();
  await expect(host).toHaveValue(full);
  await expect(copy).toBeEnabled();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await copy.click();
  await expect(page.getByRole("button", { name: "已复制" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(full);
  await expect(page.getByRole("button", { name: "检查 QQ API" })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await host.fill("https://bot.example.com");
  await host.blur();
  const panel = page.getByRole("region", { name: "接入 QQ 官方机器人" });
  if (isMobile) {
    await page.getByRole("radio", { name: "Webhook 回调" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("qqbot-setup.png") });
  } else {
    await page.setViewportSize({ width: 1440, height: 1300 });
    await panel.screenshot({ path: info.outputPath("qqbot-setup.png") });
  }
  state.rejectSecret = false;
  await panel.getByLabel("机器人 AppID", { exact: true }).fill("12345");
  await panel.getByLabel("机器人 AppSecret", { exact: true }).fill("demo-secret");
  await panel.getByRole("button", { name: "保存并连接" }).click();
  const overview = page.getByRole("region", { name: "QQ 机器人连接", exact: true });
  await expect(overview.getByText("尚未收到事件")).toBeVisible();
  await expect(host).toBeVisible();
  await expect(output).toHaveValue("https://bot.example.com" + callbackPath);
  await expect(overview.getByLabel("机器人 AppSecret", { exact: true })).toHaveValue("****");
  await expect(overview.getByRole("switch", { name: "Anelf 启动时自动连接" })).toBeChecked();
  if (isMobile) {
    await overview.getByRole("heading", { name: "Demo bot" }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: info.outputPath("qqbot-connected.png") });
  } else {
    await overview.screenshot({ path: info.outputPath("qqbot-connected.png") });
  }
});

test("reconnecting shows the live fault and preserves edits when the bot recovers", async ({ page }) => {
  const { state, values } = await mockBot(page, true);
  state.status = "reconnecting";
  state.error = "QQ gateway disconnected";
  Object.assign(values, { enabled: true, app_id: "12345", app_secret: "****", connection_mode: "websocket" });
  await page.goto("/webui/channels?channel=qqbot");
  const overview = page.getByRole("region", { name: "QQ bot connection", exact: true });
  await expect(overview.getByRole("alert")).toHaveText(state.error);
  await expect(overview.getByRole("button", { name: "Check QQ API" })).toBeDisabled();
  const appId = overview.getByLabel("Bot AppID", { exact: true });
  await appId.fill("67890");
  state.status = "running";
  state.error = "";
  await expect(overview.getByRole("heading", { name: "Demo bot" })).toBeVisible({ timeout: 10000 });
  await expect(appId).toHaveValue("67890");
  await expect(overview.getByRole("button", { name: "Check QQ API" })).toBeDisabled();
});
