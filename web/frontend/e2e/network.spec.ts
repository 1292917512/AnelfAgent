import { test, expect } from "./fixtures";

test("network diagnostics distinguish fallback, proxy failure and retry on mobile", async ({ page }, testInfo) => {
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "network/proxy", items: [
    field("proxy_enabled", "boolean", "Enable proxy", true),
  ] }] } }));
  let calls = 0;
  await page.route("**/api/network/diagnose", async (route) => {
    calls += 1;
    expect(route.request().postDataJSON()).toMatchObject({ mode: "compare", samples: 3 });
    if (calls === 1) { await route.fulfill({ status: 503, json: { detail: "Network check unavailable" } }); return; }
    const sample = { index: 1, elapsed_ms: 245, reachable: true, http_ok: true, status_code: 204, path: "proxy", source: "default", attempts: 2, error_code: null };
    await route.fulfill({ json: {
      url: "https://www.gstatic.com/generate_204", checked_at: Date.now() / 1000, elapsed_ms: 1200, configuration_changed: false,
      routes: [
        { mode: "system", status: "passed", reachable_count: 3, http_ok_count: 3, median_ms: 245, spread_ms: 20, error_code: null, samples: [1, 2, 3].map((index) => ({ ...sample, index })) },
        { mode: "direct", status: "failed", reachable_count: 0, http_ok_count: 0, median_ms: null, spread_ms: null, error_code: null, samples: [{ ...sample, path: "direct", attempts: 1, reachable: false, http_ok: false, status_code: null, error_code: "timeout" }] },
        { mode: "proxy", status: "http_error", reachable_count: 1, http_ok_count: 0, median_ms: 245, spread_ms: 0, error_code: null, samples: [{ ...sample, status_code: 403, http_ok: false, attempts: 1, error_code: "http_status" }] },
      ],
    } });
  });
  await page.goto("/webui/config?group=network/proxy");
  const panel = page.getByRole("region", { name: "Proxy & connection check" });
  await panel.getByRole("button", { name: "Run check" }).click();
  await expect(panel.getByRole("alert")).toContainText("Network check unavailable");
  await panel.getByRole("button", { name: "Run check" }).click();
  await expect(panel.getByText("Samples passed", { exact: true })).toBeVisible();
  await expect(panel.getByText("Responded · HTTP error", { exact: true })).toBeVisible();
  await expect(panel.getByText(/HTTP 403/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await panel.screenshot({ path: testInfo.outputPath("network-diagnostics.png") });
});

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
