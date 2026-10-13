import { test, expect, openWebChat } from "./fixtures";

test("a closed connection recovers missed Web replies and runtime without reloading or resending", async ({ page }) => {
  let recovered = false;
  let messageId = "";
  let submissions = 0;
  let connections = 0;
  await page.route("**/api/chat/stream", (route) => {
    connections++;
    return recovered ? route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" })
      : route.fulfill({ status: 503, body: "Temporary connection loss" });
  });
  await page.route("**/api/chat/send", (route) => {
    const body = route.request().postDataJSON() as { message_id: string };
    messageId = body.message_id;
    submissions++;
    recovered = true;
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: recovered ? [
    { id: 1, cid: messageId, role: "user", content: "Recover this reply", ts: 1 },
    { id: 2, role: "assistant", content: "Reply delivered while connection was closed", ts: 2 },
  ] : [] }));
  await page.goto("/webui/");
  await openWebChat(page);
  const chat = page.getByRole("region", { name: "Web chat", exact: true });
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Recover this reply");
  await chat.getByRole("button", { name: "Send", exact: true }).click();
  await chat.getByRole("textbox", { name: "Message", exact: true }).fill("Keep my next draft");
  await expect(chat.getByText("Reply delivered while connection was closed", { exact: true })).toBeVisible({ timeout: 15000 });
  await expect(chat.getByRole("button", { name: "Stop", exact: true })).toHaveCount(0);
  await expect(chat.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep my next draft");
  expect(submissions).toBe(1);
  expect(connections).toBeGreaterThan(1);
});
