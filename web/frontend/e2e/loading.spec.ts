import { test, expect } from "./fixtures";

test("dialogs use short motion without animating layout and respect reduced motion", async ({ page }) => {
  await page.goto("/webui/tasks");
  const edit = page.getByRole("button", { name: "Edit", exact: true });
  await expect(edit).not.toHaveCSS("transition-property", "all");
  await edit.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  const duration = await dialog.evaluate((element) => Number.parseFloat(getComputedStyle(element).animationDuration));
  expect(duration).toBeLessThanOrEqual(0.2);
  await expect(page.locator(".dialog-overlay")).toHaveCSS("backdrop-filter", "none");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(edit).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await edit.click();
  await expect(dialog).toHaveCSS("animation-name", "none");
  const bounds = await dialog.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
});

for (const route of ["models", "memory", "data", "thinking"]) {
  test(`${route} opens without downloading inactive editors or graphs`, async ({ page }, testInfo) => {
    const scripts: string[] = [];
    page.on("request", (request) => {
      if (request.resourceType() === "script") scripts.push(new URL(request.url()).pathname);
    });
    await page.goto(`/webui/${route}`);
    await expect(page.locator("#main-content")).toBeVisible();
    await expect(page.getByRole("status", { name: "Loading...", exact: true })).toHaveCount(0);
    const assets = scripts.filter((path) => path.startsWith("/webui/assets/"));
    if (assets.length) expect(assets.length).toBeLessThanOrEqual(35);
    expect(scripts.some((path) => /(?:GraphPanel|FlowView|QueryPanel|RowEditModal|RowDetailModal|FileEditor)[-.]/.test(path))).toBe(false);
    await testInfo.attach("initial-scripts", { body: JSON.stringify(scripts, null, 2), contentType: "application/json" });
  });
}

test("a slow graph download keeps page tabs usable and loads on demand", async ({ page }) => {
  await page.route("**/api/memory/graph**", (route) => route.fulfill({ json: { nodes: [], edges: [], stats: { nodes: 0, edges: 0, node_types: {}, top_predicates: {} } } }));
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let requested = false;
  await page.route(/\/(?:assets\/GraphPanel-[^/]+\.js|src\/pages\/memory\/graph\/GraphPanel\.tsx)(?:\?.*)?$/, async (route) => {
    requested = true;
    await gate;
    await route.continue();
  });
  try {
    await page.goto("/webui/memory");
    await expect(page.getByRole("tab", { name: "Overview", exact: true })).toBeVisible();
    expect(requested).toBe(false);
    await page.getByRole("tab", { name: "Graph", exact: true }).click();
    await expect.poll(() => requested).toBe(true);
    await expect(page.getByRole("status", { name: "Loading...", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await expect(page.getByRole("tab", { name: "Overview", exact: true })).toHaveAttribute("aria-selected", "true");
    release();
    await page.getByRole("tab", { name: "Graph", exact: true }).click();
    await expect(page.getByRole("button", { name: "Add relation", exact: true })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
  } finally { release(); }
});
