import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { UiStateReport } from "../src/lib/types";

test("workspace keeps folders and drafts across focused panels and viewport changes", async ({ page, isMobile }, info) => {
  let reported: UiStateReport | undefined;
  await page.route("**/api/chat/ui-state", (route) => {
    reported = (route.request().postDataJSON() as { state: UiStateReport }).state;
    return route.fulfill({ json: { status: "ok" } });
  });
  await page.route("**/api/workspace/tree?**", (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    return route.fulfill({ json: { path, children: path ? [{ name: "note.txt", path: "docs/note.txt", type: "file", size: 40, modified: 0 }]
      : [{ name: "docs", path: "docs", type: "dir", has_children: true, modified: 0 }], truncated: false } });
  });
  await page.route("**/api/chat/history?**", (route) => route.fulfill({ json: [
    { id: 1, role: "user", content: "Review the workspace and help me refine the next release.", ts: 1770000000 },
    { id: 2, role: "assistant", content: "## A clearer workspace\n\nI’ll start with the files, then review the implementation and its tests.\n\n- Preserve existing behavior\n- Keep each module focused\n- Verify desktop and touch interactions\n\nYou can inspect [the working notes](./docs%2Fnote.txt) alongside this conversation.", ts: 1770000010 },
  ] }));
  await page.goto("/webui/");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Keep this draft while I review the files.");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: /^docs$/ }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("workspace content");
  await page.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText("Draft survives layout changes");
  if (!isMobile) {
    await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
    const conversation = await page.locator(".conversation-pane").boundingBox();
    expect(conversation!.width).toBeGreaterThanOrEqual(439);
    await page.setViewportSize({ width: 1800, height: 1050 });
    await page.getByRole("button", { name: "Panels", exact: true }).click();
    await page.getByRole("tab", { name: "Tasks", exact: true }).click();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("treeitem").filter({ hasText: "note.txt" })).toBeVisible();
  } else {
    await expect(page.getByRole("dialog")).toHaveCount(1);
  }
  await expect.poll(() => reported).toMatchObject({ open_file: "docs/note.txt", has_draft: true, left_open: !isMobile });
  await page.screenshot({ path: info.outputPath("workspace-editor.png") });
  await page.getByRole("button", { name: "Collapse panel (tabs kept)", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep this draft while I review the files.");
  if (isMobile) await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await expect(page.getByRole("treeitem").filter({ hasText: "note.txt" })).toBeVisible();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("Draft survives layout changes");
  await page.getByRole("button", { name: "Collapse panel (tabs kept)", exact: true }).click();
  await expect.poll(() => reported).toMatchObject({ open_file: null, active_file: "docs/note.txt", left_open: !isMobile });
  await page.screenshot({ path: info.outputPath("workspace-conversation.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("");
  await expect.poll(() => reported).toMatchObject({ has_draft: false });
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect.poll(() => reported).toMatchObject({ page: "/dashboard", left_open: false, dock_open: false, open_file: null });
});

test("touch input keeps Enter as a newline and stays above the virtual keyboard", async ({ page, isMobile }, info) => {
  test.skip(!isMobile, "Touch input");
  let sends = 0;
  await page.route("**/api/chat/send", (route) => { sends++; return route.fulfill({ json: { ok: true } }); });
  await page.goto("/webui/");
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  await input.fill("First line");
  await input.press("Enter");
  await expect(input).toHaveValue("First line\n");
  expect(sends).toBe(0);
  await page.evaluate(() => {
    Object.defineProperty(window.visualViewport, "height", { configurable: true, get: () => window.innerHeight - 320 });
    window.visualViewport?.dispatchEvent(new Event("resize"));
  });
  await expect(page.locator(".mobile-nav")).toBeHidden();
  const send = page.getByRole("button", { name: "Send", exact: true });
  const bounds = await send.boundingBox();
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(await page.evaluate(() => window.visualViewport!.height));
  await page.screenshot({ path: info.outputPath("workspace-keyboard.png") });
  await send.click();
  await expect.poll(() => sends).toBe(1);
});

test("navigation and appearance remain discoverable on a small screen", async ({ page, isMobile }, info) => {
  await page.goto("/webui/tasks");
  if (isMobile) await page.getByRole("button", { name: "More", exact: true }).click();
  else await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  const filter = page.getByRole("textbox", { name: "Filter navigation…" });
  await filter.fill("context");
  const contextLink = page.getByRole("navigation").getByRole("link", { name: "Context", exact: true });
  await expect(contextLink).toBeVisible();
  await filter.fill("");
  await page.screenshot({ path: info.outputPath("navigation.png") });
  if (isMobile) await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Appearance & language", exact: true }).click();
  await page.getByRole("button", { name: /Switch to dark|Dark theme|Use dark|dark mode/i }).click();
  await page.keyboard.press("Escape");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: info.outputPath("tasks-dark.png") });
  const results = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(results.violations.map((item) => ({ id: item.id, nodes: item.nodes.map((node) => node.target) }))).toEqual([]);
});

test("mobile configuration groups are searchable and selection keeps the deep link", async ({ page, isMobile }, info) => {
  test.skip(!isMobile, "Mobile group picker");
  await page.goto("/webui/config");
  await page.getByRole("button", { name: "Choose configuration group", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("textbox", { name: "Find a module or group…" }).fill("telegram");
  await page.screenshot({ path: info.outputPath("config-groups.png") });
  await dialog.getByRole("button", { name: /telegram/i }).click();
  await expect(dialog).toBeHidden();
  await expect(page).toHaveURL(/group=adapter%2Ftelegram/);
});

test("rich messages keep lists and wide tables readable on narrow screens", async ({ page, isMobile }, info) => {
  if (isMobile) await page.setViewportSize({ width: 360, height: 740 });
  await page.route("**/api/chat/history?**", (route) => route.fulfill({ json: [{ id: 1, role: "assistant", ts: 1770000000,
    content: "## Release checks\n\n- Preserve the original data\n- Validate touch interactions\n\n| Module | Desktop | Mobile | Keyboard | Screen reader | Status |\n| --- | --- | --- | --- | --- | --- |\n| Workspace | Ready | Ready | Ready | Ready | Verified |\n\n" + "A long file reference: `" + "directory/".repeat(30) + "note.md`",
  }] }));
  await page.goto("/webui/");
  const markdown = page.locator(".markdown-content").first();
  await expect(markdown.locator("ul")).toHaveCSS("list-style-type", "disc");
  await expect(markdown.locator("table")).toContainText("Workspace");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (isMobile) expect(await page.locator(".markdown-table").evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("rich-message.png") });
});

test("form sheets stay in view on phones and small tablets", async ({ page, isMobile }) => {
  test.skip(!isMobile, "Touch form geometry");
  await page.goto("/webui/tasks");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  for (const width of [700, 390]) {
    await page.setViewportSize({ width, height: 800 });
    await expect.poll(async () => {
      const bounds = await dialog.boundingBox();
      return !!bounds && bounds.y >= 0 && bounds.x >= 0 && bounds.y + bounds.height <= 801 && bounds.x + bounds.width <= width + 1;
    }).toBe(true);
  }
  await dialog.getByRole("spinbutton").focus();
  await page.evaluate(() => {
    Object.defineProperty(window.visualViewport, "height", { configurable: true, get: () => 480 });
    window.visualViewport?.dispatchEvent(new Event("resize"));
  });
  await expect.poll(async () => {
    const bounds = await dialog.getByRole("button", { name: "Save", exact: true }).boundingBox();
    return !!bounds && bounds.y >= 0 && bounds.y + bounds.height <= 480;
  }).toBe(true);
});
