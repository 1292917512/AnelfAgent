import { test, expect } from "../../../../web/frontend/e2e/fixtures";

test("GitHub subscriptions and message content save as structured arrays", async ({ page, isMobile }, info) => {
  if (isMobile) await page.setViewportSize({ width: 390, height: 844 });
  const items = [
    { key: "github_enabled", type: "boolean", value: false, default: false, editable: true, schema: { type: "boolean", section: "reception" } },
    { key: "github_default_events", type: "json", value: ["push"], default: ["push"], editable: true,
      schema: { type: "array", section: "subscriptions", options: ["push", "issues", "discussion_comment", "pull_request_review_comment"], items: { type: "string" } } },
    { key: "github_repos", type: "json", value: [], default: [], editable: true, schema: {
      type: "array", section: "subscriptions", items: { type: "object", required: ["owner", "repo"], properties: {
        owner: { type: "string" }, repo: { type: "string" }, priority_boost: { type: "boolean", default: false },
        events: { type: "array", options: ["push", "issues"], items: { type: "string" } },
      } },
    } },
    { key: "github_message_fields", type: "json", value: ["body", "url"], default: ["body", "url"], editable: true,
      schema: { type: "array", section: "messages", options: ["body", "url", "context"], items: { type: "string" } } },
  ];
  let saved: Record<string, unknown> = {};
  await page.route("**/api/adapters/", route => route.fulfill({ json: { ready: true, adapters: [{ key: "github", name: "GitHub", status: "stopped", status_display: "Stopped" }] } }));
  await page.route("**/api/config/meta", route => route.fulfill({ json: { groups: [{ group: "adapter/github", items }] } }));
  await page.route("**/api/config/meta/channels/github", route => {
    saved = route.request().postDataJSON();
    for (const item of items) if (item.key in saved) Reflect.set(item, "value", saved[item.key]);
    return route.fulfill({ json: { values: saved } });
  });
  await page.goto("/webui/channels?channel=github");
  await page.getByRole("tab", { name: "Repositories & events" }).click();
  await page.getByLabel("discussion_comment", { exact: true }).check();
  await page.getByRole("button", { name: "Add Repository subscriptions", exact: true }).click();
  await page.getByRole("textbox", { name: "Owner", exact: true }).fill("nonebot");
  await page.getByRole("textbox", { name: "Repo", exact: true }).fill("adapter-github");
  await page.getByRole("switch", { name: "Priority boost" }).check();
  await page.getByRole("tab", { name: "Message content" }).click();
  await page.getByLabel("Body summary", { exact: true }).uncheck();
  await page.screenshot({ path: info.outputPath("github-content.png"), fullPage: true });
  await page.getByRole("button", { name: "Save configuration", exact: true }).click();
  await expect.poll(() => saved.github_default_events).toEqual(["push", "discussion_comment"]);
  expect(saved.github_message_fields).toEqual(["url"]);
  expect(saved.github_repos).toEqual([{ owner: "nonebot", repo: "adapter-github", priority_boost: true, events: [] }]);
  await expect(page.getByText("Configuration saved", { exact: true })).toBeVisible();
  const dimensions = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: innerWidth }));
  expect(dimensions.width).toBeLessThanOrEqual(dimensions.viewport);
});
