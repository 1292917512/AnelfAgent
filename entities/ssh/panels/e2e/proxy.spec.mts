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
    const failed = calls.length === 3;
    await route.fulfill({ json: {
      ok: !failed, exit_code: failed ? 1 : 0, stdout: failed ? "NO_SUDO\n/mihomo" : "test result",
      stderr: "", connection: "test-host", truncated: false, work_dir: "/home/test",
      requested_work_dir: "", work_dir_tracking: true, login_user: "test", host: "192.168.1.10",
      proxy_mode: "direct", elapsed_ms: 23,
      error: failed ? "Exit code 1; stdout: NO_SUDO /mihomo" : null,
      hint: failed ? "The command may have partially succeeded. Check the result before retrying." : null,
    } });
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
  await expect(page.getByRole("status")).toContainText("Exit code 1; stdout: NO_SUDO");
  await expect(page.getByRole("status")).toContainText("partially succeeded");
  await expect(page.getByText("test@192.168.1.10", { exact: true })).toBeVisible();
  await expect(page.getByText("/home/test", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("status").scrollIntoViewIfNeeded();
  await expect(page.getByRole("status")).toBeInViewport();
  await page.screenshot({ path: testInfo.outputPath("ssh-route.png"), fullPage: true });
  let removes = 0;
  let finish: () => void = () => {};
  const pending = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/entity/ssh/connections/test-host", async (route) => {
    removes++;
    await pending;
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.getByRole("tab", { name: "Connections", exact: true }).click();
  const remove = page.getByRole("button", { name: "Delete", exact: true });
  await remove.click();
  const confirmation = page.getByRole("dialog", { name: "Delete", exact: true });
  await expect(confirmation).toContainText("test-host");
  await confirmation.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(confirmation).toBeHidden();
  await expect(remove).toBeFocused();
  expect(removes).toBe(0);
  await remove.click();
  await confirmation.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect.poll(() => removes).toBe(1);
  await expect(confirmation.getByRole("button", { name: "Confirm", exact: true })).toBeDisabled();
  await expect(confirmation.getByRole("button", { name: "Cancel", exact: true })).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(confirmation).toBeVisible();
  finish();
  await expect(confirmation).toBeHidden();
});
