import { test, expect } from "../../../../web/frontend/e2e/fixtures";

test("module registers its library route and data tab", async ({ page }) => {
  await page.route("**/api/entity/sticker**", (route) => route.fulfill({ json: {
    items: [], total: 0, page: 1, page_size: 24, stickers: 0, indexed_images: 0, total_uses: 0,
  } }));
  await page.goto("/webui/stickers");
  await expect(page.getByRole("heading", { name: "Sticker Library", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Upload Sticker", exact: true })).toBeVisible();
  await page.goto("/webui/data?tab=stickers");
  await expect(page.getByRole("tab", { name: "Sticker Library", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("button", { name: "Upload Sticker", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});
