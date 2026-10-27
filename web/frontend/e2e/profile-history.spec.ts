import { test, expect } from "./fixtures";

test("profile edits preserve drafts on conflict and expose recoverable history", async ({ page }) => {
  const profile = { scope_type: "agent", scope_id: "self", personality: "Existing profile", revision: "r1", conv_num: 1200, conv_update_num: 17, updated_ts_ns: 123 };
  const operation = { id: 1, operation_id: "operation-1", scope_type: "agent", scope_id: "self", action: "update", actor: "reflection_promote", reason: "Verified evidence", source_memory_id: 42, created_at: 1700000000, synced: false, error: "Temporary sync failure" };
  let submitted: unknown;
  await page.route("**/api/memory/entities**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/aliases")) return route.fulfill({ json: [] });
    if (url.pathname.endsWith("/timeline")) return route.fulfill({ json: { items: [operation], next_cursor: null } });
    if (url.pathname.endsWith("/sync")) {
      operation.synced = true; operation.error = "";
      return route.fulfill({ json: { revision: "r2", changed: false, synced: true } });
    }
    if (url.pathname.includes("/operations/")) return route.fulfill({ json: { ...operation, before_content: "Existing profile", after_content: "Updated " + "evidence".repeat(100), diff: "--- before\n+++ after\n@@ -1 +1 @@\n-Existing profile\n+Updated evidence" } });
    if (route.request().method() === "PUT") {
      submitted = route.request().postDataJSON();
      profile.revision = "r2"; profile.personality = "Concurrent AI revision";
      return route.fulfill({ status: 409, json: { detail: "Profile changed, reload before editing" } });
    }
    return route.fulfill({ json: [profile] });
  });
  await page.goto("/webui/memory?tab=entity");
  await page.getByRole("button", { name: /self \(agent\)/ }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("textbox", { name: "Entity Profiles", exact: true }).fill("My careful draft");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Profile changed" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Entity Profiles", exact: true })).toHaveValue("My careful draft");
  expect(submitted).toMatchObject({ personality: "My careful draft", revision: "r1" });
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByText("Concurrent AI revision", { exact: true }).last()).toBeVisible();
  await page.getByRole("button", { name: "Retry sync", exact: true }).click();
  await expect(page.getByRole("button", { name: "Retry sync", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "View changes and full text" }).click();
  await page.getByRole("tab", { name: "Full text after" }).click();
  await expect(page.getByText("Updated " + "evidence".repeat(100), { exact: true })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test("memory editing loads full text and refreshes operation history after saving", async ({ page }) => {
  const fullText = "A complete fact with conditions. ".repeat(100);
  let submitted: { content: string; expected_version: number } | undefined;
  await page.route("**/api/memory/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/timeline")) return route.fulfill({ json: { items: submitted ? [{ id: 1, memory_id: 12, action: "update", actor: "web", detail: "Saved full memory", timestamp: 1700000000, data: {} }] : [], next_cursor: null, retention_days: 30 } });
    if (path.endsWith("/paginated")) return route.fulfill({ json: { items: [{ id: 12, type: "semantic", content: "Short preview", importance: 0.6, version: 7 }], total: 1, pages: 1 } });
    if (path.endsWith("/12")) {
      if (route.request().method() === "PUT") {
        submitted = route.request().postDataJSON();
        return route.fulfill({ json: { ok: true } });
      }
      return route.fulfill({ json: { id: 12, content: fullText, importance: 0.6, version: 7, tags: [], metadata: {}, memory_type: "semantic" } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto("/webui/memory?tab=ltm");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Memory content" })).toHaveValue(fullText);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Saved full memory", { exact: true })).toBeVisible();
  expect(submitted).toEqual(expect.objectContaining({ content: fullText, expected_version: 7 }));
});
