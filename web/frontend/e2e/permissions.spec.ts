import { test, expect } from "./fixtures";

test("permissions use AI review and show real audit fields without human actions", async ({ page }, testInfo) => {
  const obsolete: string[] = [];
  page.on("request", (request) => {
    if (/\/approvals\/(pending|policies|[^/]+\/(approve|deny))/.test(request.url())) obsolete.push(request.url());
  });
  let rules = [{ id: "rule-1", pattern: "update_entity_config", effect: "ask", scope: "global", users: [], risk_level: "critical", description: "Review settings changes", enabled: true, created_at: 1, created_by: "webui" }];
  await page.route("**/api/approvals/rules", async (route) => {
    if (route.request().method() === "PUT") {
      rules = route.request().postDataJSON().rules;
      await route.fulfill({ json: { status: "ok", count: rules.length } });
    } else await route.fulfill({ json: { default_effect: "allow", default_risk: "low", rules, load_error: "" } });
  });
  await page.route("**/api/approvals/stats", (route) => route.fulfill({ json: { total: 3, by_outcome: { guardian_approved: 1, guardian_bypass: 1, denied: 1 } } }));
  await page.route("**/api/approvals/history?*", (route) => route.fulfill({ json: {
    offset: 0, limit: 50, history: [{ id: 7, ts_ns: 1791631200000000000, tool_name: "update_entity_config", outcome: "guardian_bypass", decided_by: "system", reason: "Review exceeded its total deadline", channel_id: "qq", chat_id: "chat-1", user_id: "123", risk_level: "critical", matched_rule: "meta:risk", args_json: '{"key":"smart_home_ha_url","value":""}' }],
  } }));
  await page.goto("/webui/approvals");
  await expect(page.getByRole("heading", { name: "Permissions & audit" })).toBeVisible();
  const pattern = page.getByRole("textbox", { name: "Tool Name Pattern" });
  await expect(pattern).toHaveValue("update_entity_config");
  await expect(page.getByRole("combobox", { name: "Rule effect" })).toHaveValue("ask");
  await pattern.fill("update_entity_config*");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await expect(pattern).toHaveValue("update_entity_config*");
  await page.getByRole("tab", { name: "History" }).click();
  const record = page.getByRole("button", { name: /update_entity_config/ });
  await expect(record).toContainText("qq · 123");
  await record.click();
  await expect(page.getByText("Review exceeded its total deadline")).toBeVisible();
  await expect(page.getByText("meta:risk", { exact: true })).toBeVisible();
  await expect(page.getByText(/smart_home_ha_url/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(obsolete).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("permissions.png"), fullPage: true });
});
