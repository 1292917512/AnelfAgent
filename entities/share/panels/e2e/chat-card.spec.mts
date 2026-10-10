import { test, expect, openWebChat } from "../../../../web/frontend/e2e/fixtures";

test("chat dispatches registered module content and retains fallback text", async ({ page }) => {
  const events = [
    { chat_id: "default", extension: { type: "share.link", fallback: "report.txt /report", payload: {
      token: "example-token", url: "/api/entity/share/d/example-token", file_name: "report.txt", share_type: "file", media_kind: "", file_size: 100,
    } } },
    { chat_id: "default", extension: { type: "uninstalled.module", payload: {}, fallback: "Module content remains readable" } },
    { chat_id: "another-chat", extension: { type: "uninstalled.module", payload: {}, fallback: "Private to another conversation" } },
    { extension: { type: "uninstalled.module", payload: {}, fallback: "Missing destination must not be shown" } },
  ];
  await page.route("**/api/chat/stream", (route) => route.fulfill({ contentType: "text/event-stream", body: events.map((data) => `event: extension\ndata: ${JSON.stringify(data)}\n\n`).join("") }));
  await page.goto("/webui/");
  await openWebChat(page);
  await expect(page.getByText("report.txt", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("Module content remains readable").first()).toBeVisible();
  await expect(page.getByText("Private to another conversation")).toHaveCount(0);
  await expect(page.getByText("Missing destination must not be shown")).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
