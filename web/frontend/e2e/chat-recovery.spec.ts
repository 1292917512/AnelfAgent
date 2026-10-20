import { test, expect, openWebChat, closeWebChatOverlay, installEventStream } from "./fixtures";

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
  await page.route("**/api/workspace/activity", (route) => route.fulfill({ json: { epoch: "recovery", revision: 1, runs: recovered ? [{
    id: "recovered", revision: 1, kind: "conversation", scope: "", origin_scope: "", parent_id: "", owner_id: "", actor: "", label: "Recovered execution", input: "",
    source: { channel: "webui", kind: "user", target: "web_user", session: "default", scope: "" },
    status: "completed", started_at: 1, updated_at: 2, ended_at: 2, entry_count: 0, truncated: false, entries: [],
  }] : [] } }));
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

test("a silent live stream reports staleness and reconnects without losing the draft", async ({ page }) => {
  await installEventStream(page);
  await page.clock.install();
  await page.goto("/webui/");
  await openWebChat(page);
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  await input.fill("Preserve during recovery");
  await closeWebChatOverlay(page);
  const initialConnections = await page.evaluate(() => Number(Reflect.get(window, "testStreamConnections")));
  await page.clock.runFor(85_000);
  for (let attempt = 0; attempt < 20; attempt++) {
    if ((await page.locator(".activity-connection").textContent())?.includes("No recent live updates")) break;
    await page.clock.runFor(1000);
  }
  await expect(page.getByRole("region", { name: "Global execution", exact: true })).toContainText("No recent live updates");
  await page.clock.runFor(5010);
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "testStreamConnections"))).toBe(initialConnections + 1);
  await openWebChat(page);
  await expect(input).toHaveValue("Preserve during recovery");
  await closeWebChatOverlay(page);
  await expect(page.getByRole("button", { name: "Synchronize live activity", exact: true })).toBeVisible();
});
