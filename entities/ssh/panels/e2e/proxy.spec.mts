import { test, expect } from "../../../../web/frontend/e2e/fixtures";

test("SSH route override keeps the saved default and exposes configuration links", async ({ page }, testInfo) => {
  await page.route("**/api/entities/ssh", (route) => route.fulfill({ json: {
    name: "ssh", group: "ssh", type: "tool", source: "entities", enabled: true,
    description: "SSH", config_group: "entity/ssh", tools: [], apis: [], providers: [],
    manifest: { display_name: "SSH", description: "SSH", version: "1.0.0" },
  } }));
  await page.route("**/api/entities/ssh/config", (route) => route.fulfill({ json: { config_group: "entity/ssh", items: [] } }));
  await page.route("**/api/entity/ssh/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }));
  await page.route("**/api/entity/ssh/connections", (route) => route.fulfill({ json: { default: "test-host", connections: [{
    name: "test-host", host: "192.168.1.10", port: 22, username: "test", description: "Local test",
    status: "disconnected", proxy_mode: "direct", last_error: "", connected_at: 0, last_used_at: 0,
    is_default: true, has_password: true, has_key: false,
  }] } }));
  const calls: Record<string, unknown>[] = [];
  await page.route("**/api/entity/ssh/connections/test-host/exec", async (route) => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({ json: { ok: true, exit_code: 0, stdout: "test result", stderr: "", connection: "test-host", truncated: false } });
  });
  await page.goto("/webui/entities/ssh");
  await page.getByRole("tab", { name: "Run Command", exact: true }).click();
  const mode = page.getByLabel("Connection route for this command");
  await expect(mode).toHaveValue("");
  await expect(page.getByRole("link", { name: "SSH defaults" })).toHaveAttribute("href", /key=ssh_proxy_mode/);
  await page.getByPlaceholder("uname -a && df -h").fill("echo test");
  const run = page.getByRole("button", { name: "Run", exact: true });
  await run.click();
  await expect(page.getByText("test result", { exact: true })).toBeVisible();
  expect(calls[0]).not.toHaveProperty("proxy_mode");
  await mode.selectOption("proxy");
  await run.click();
  await expect.poll(() => calls.length).toBe(2);
  expect(calls[1]).toMatchObject({ proxy_mode: "proxy", command: "echo test" });
  await mode.selectOption("direct");
  await run.click();
  await expect.poll(() => calls.length).toBe(3);
  expect(calls[2]).toMatchObject({ proxy_mode: "direct" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("ssh-route.png"), fullPage: true });
});
