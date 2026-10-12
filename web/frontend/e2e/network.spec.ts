import { test, expect } from "./fixtures";

const field = (key: string, type: string, description: string, value: unknown, options: string[] = []) => ({
  key, type, description, value, default: value, editable: true, options, min: null, max: null,
});

test("network rules support deep links, multiline editing and actionable validation", async ({ page }, testInfo) => {
  const items = [
    field("proxy_enabled", "boolean", "Enable global proxy routing", true),
    field("http_proxy", "string", "Global proxy address", "http://127.0.0.1:7890"),
    field("proxy_default_mode", "enum", "Default routing policy", "fallback", ["fallback", "direct", "proxy"]),
    field("proxy_rules", "text", "Site rules, first match wins: DOMAIN / DOMAIN-SUFFIX / IP-CIDR", "DOMAIN-SUFFIX,telegram.org,PROXY\nDOMAIN-SUFFIX,internal.example.com,DIRECT"),
  ];
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "network/proxy", items }] } }));
  await page.route("**/api/config/meta/proxy_rules", (route) => route.fulfill({ status: 400, json: { detail: "Rule line 1: unsupported action" } }));
  await page.goto("/webui/config?key=proxy_rules");
  const rules = page.getByRole("textbox", { name: /Site rules/ });
  await expect(rules).toBeVisible();
  await rules.fill("DOMAIN,example.com,INVALID");
  await rules.blur();
  await expect(page.locator("#config-item-proxy_rules").getByRole("alert")).toContainText("Rule line 1");
  await expect(rules).toHaveValue("DOMAIN,example.com,INVALID");
  await rules.fill("DOMAIN-SUFFIX,telegram.org,PROXY\nDOMAIN-SUFFIX,internal.example.com,DIRECT");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("network-settings.png"), fullPage: true });
});
