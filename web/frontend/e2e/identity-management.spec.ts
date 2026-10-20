import { test, expect } from "./fixtures";

for (const domain of ["speakers", "persons"] as const) {
  test(`${domain} paginate and search without losing drafts, and merge across pages`, async ({ page }) => {
    const resource = domain === "speakers" ? "/audio/speakers" : "/face/persons";
    const reads: { keyword: string; offset: number }[] = [];
    const merges: unknown[] = [];
    await page.clock.install();
    await page.route("**/api/face/status*", (route) => route.fulfill({ json: {
      engine: { configured: false, reachable: false, health: null, endpoint: "" }, stats: {}, thresholds: { match: 0.7, merge: 0.8, separation: 0.1 },
    } }));
    await page.route(`**/api${resource}**`, (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/confirm")) return route.fulfill({ status: 422, json: { detail: "Identity validation rejected" } });
      if (url.pathname.endsWith("/merge")) { merges.push(route.request().postDataJSON()); return route.fulfill({ json: {} }); }
      const keyword = url.searchParams.get("keyword") ?? "";
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 50);
      reads.push({ keyword, offset });
      const items = Array.from({ length: keyword ? 1 : Math.min(limit, 121 - offset) }, (_, index) => {
        const id = keyword ? 1000 : offset + index + 1;
        return { id, name: keyword ? "Matched Profile" : `Profile ${id}`, speaker_key: `key-${id}`, person_key: `key-${id}`,
          role: "", status: "pending", sample_count: 2, match_count: 3, total_audio_ms: 1000, last_seen_ns: 0,
          channels: {}, entity_scope: "", threshold: null, notes: "", first_seen_ns: 0, archived: false, anchor_weight: 1 };
      });
      return route.fulfill({ json: { items, total: keyword ? 1 : 121, limit, offset } });
    });
    await page.goto(domain === "speakers" ? "/webui/sound?tab=speakers" : "/webui/vision?tab=face");
    if (domain === "persons") await page.getByRole("button", { name: "Persons", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Profile 1", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Profile 51", exact: true })).toBeVisible();
    expect(reads[reads.length - 1]?.offset).toBe(50);
    const search = page.getByRole("textbox", { name: /name\/role\/key/ });
    await search.fill("needle");
    await page.clock.runFor(260);
    await expect(page.getByRole("heading", { name: "Matched Profile", exact: true })).toBeVisible();
    expect(reads[reads.length - 1]).toEqual({ keyword: "needle", offset: 0 });
    const card = page.locator(".surface-card").filter({ has: page.getByRole("heading", { name: "Matched Profile", exact: true }) });
    await card.getByRole("button", { name: "Confirm", exact: true }).click();
    const dialog = page.getByRole("dialog");
    const name = dialog.getByPlaceholder("Name", { exact: true });
    await name.fill("Keep this draft");
    await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
    await expect(page.getByText("Identity validation rejected", { exact: true })).toHaveCount(1);
    const previousReads = reads.length;
    await page.clock.runFor(15_010);
    await expect.poll(() => reads.length).toBeGreaterThan(previousReads);
    await expect(name).toHaveValue("Keep this draft");
    await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
    await search.fill("");
    await page.clock.runFor(260);
    const first = page.locator(".surface-card").filter({ has: page.getByRole("heading", { name: "Profile 1", exact: true }) });
    await first.getByRole("button", { name: "Merge", exact: true }).click();
    await dialog.getByRole("button", { name: "Next", exact: true }).click();
    await dialog.getByRole("combobox").selectOption("51");
    await dialog.getByRole("button", { name: "Merge", exact: true }).click();
    await expect.poll(() => merges).toEqual([{ source_id: 1, target_id: 51 }]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
