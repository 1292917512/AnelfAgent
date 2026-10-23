import AxeBuilder from "@axe-core/playwright";
import { test, expect, openWebChat, closeWebChatOverlay } from "./fixtures";
import type { UiStateReport } from "../src/lib/types";

test("shared selects animate inside dialogs without closing the editor or losing the draft", async ({ page }, info) => {
  await page.goto("/webui/tasks");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const editor = page.getByRole("dialog");
  const select = editor.locator("select").first();
  await select.click();
  await expect.poll(() => select.evaluate(element => element.matches(":open"))).toBe(true);
  await expect(select).toHaveCSS("appearance", "base-select");
  await page.screenshot({ path: info.outputPath("select-dialog.png") });
  await page.keyboard.press("Escape");
  await expect.poll(() => select.evaluate(element => element.matches(":open"))).toBe(false);
  await expect(editor).toBeVisible();
  await expect(select).toBeFocused();
  await select.click();
  await select.getByRole("option").nth(1).click();
  await expect.poll(() => select.evaluate(element => element.matches(":open"))).toBe(false);
  await expect(editor).toBeVisible();
  await editor.getByRole("spinbutton").fill("0.3");
  await editor.getByRole("button", { name: "Cancel", exact: true }).click();
  const confirmation = page.getByRole("dialog", { name: "Unsaved changes", exact: true });
  await expect(confirmation).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(confirmation).toBeHidden();
  await expect(editor.getByRole("spinbutton")).toHaveValue("0.3");
  await expect(editor.getByRole("button", { name: "Cancel", exact: true })).toBeFocused();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await select.click();
  expect(await select.evaluate(element => getComputedStyle(element, "::picker(select)").transitionDuration)).toBe("0s");
  await page.keyboard.press("Escape");
  await expect(editor).toBeVisible();
});

test("overflowing memory tabs stay reachable by arrows, wheel, touch and keyboard", async ({ page, isMobile }, info) => {
  await page.route("**/api/memory/health", route => route.fulfill({ json: { type_counts: {}, total_memories: 0 } }));
  await page.route("**/api/memory/index/status", route => route.fulfill({ json: {} }));
  await page.setViewportSize({ width: isMobile ? 390 : 700, height: 850 });
  await page.goto("/webui/memory");
  const strip = page.getByRole("tablist");
  const previous = page.getByRole("button", { name: "Scroll tabs left", exact: true });
  const next = page.getByRole("button", { name: "Scroll tabs right", exact: true });
  const last = strip.getByRole("tab").last();
  const selectedIsVisible = () => strip.evaluate((element) => {
    const selected = element.querySelector('[aria-selected="true"]')?.getBoundingClientRect();
    const bounds = element.getBoundingClientRect();
    return !!selected && selected.left >= bounds.left - 1 && selected.right <= bounds.right + 1;
  });
  await expect(previous).toBeDisabled();
  await expect(next).toBeEnabled();
  if (isMobile) {
    const bounds = await strip.boundingBox();
    if (!bounds) throw new Error("Tab strip is missing");
    const session = await page.context().newCDPSession(page);
    const x = bounds.x + bounds.width - 15;
    const y = bounds.y + bounds.height / 2;
    await session.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    for (let step = 1; step <= 5; step++) {
      await session.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: x - step * 30, y }] });
    }
    await session.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await session.detach();
  } else {
    await strip.hover();
    await page.mouse.wheel(0, 220);
  }
  await expect.poll(() => strip.evaluate(element => element.scrollLeft)).toBeGreaterThan(50);
  let lastPosition = -1;
  let stableSamples = 0;
  await expect.poll(async () => {
    const position = await strip.evaluate(element => element.scrollLeft);
    stableSamples = Math.abs(position - lastPosition) < 1 ? stableSamples + 1 : 0;
    lastPosition = position;
    return stableSamples;
  }, { intervals: [100] }).toBeGreaterThanOrEqual(3);
  for (let step = 0; step < 12 && await next.isEnabled(); step++) {
    const target = await strip.evaluate(element => Math.min(element.scrollLeft + element.clientWidth * 0.8, element.scrollWidth - element.clientWidth));
    await next.click();
    await expect.poll(() => strip.evaluate((element, end) => Math.abs(element.scrollLeft - end), target)).toBeLessThanOrEqual(1);
  }
  await expect(next).toBeDisabled();
  await last.click();
  await expect(page).toHaveURL(/tab=cognee/);
  await expect.poll(selectedIsVisible).toBe(true);
  await page.screenshot({ path: info.outputPath("memory-tabs-end.png") });
  await last.press("Home");
  await expect(previous).toBeDisabled();
  await expect.poll(selectedIsVisible).toBe(true);
  await strip.getByRole("tab").first().press("End");
  await expect(last).toBeFocused();
  await expect.poll(selectedIsVisible).toBe(true);
  await page.setViewportSize({ width: 3200, height: 1000 });
  await expect(next).toBeHidden();
  await page.setViewportSize({ width: 360, height: 800 });
  await expect(next).toBeDisabled();
  await expect.poll(selectedIsVisible).toBe(true);
  await page.reload();
  await expect(last).toHaveAttribute("aria-selected", "true");
  await expect.poll(selectedIsVisible).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await previous.click();
  await expect(next).toBeEnabled();
});

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
  await openWebChat(page);
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Keep this draft while I review the files.");
  await closeWebChatOverlay(page);
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: /^docs$/ }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("workspace content");
  await page.locator(".cm-content").click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText("Draft survives layout changes");
  if (!isMobile) {
    await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
    const execution = await page.getByRole("region", { name: "Global execution", exact: true }).boundingBox();
    expect(execution!.width).toBeGreaterThanOrEqual(499);
    await page.setViewportSize({ width: 2250, height: 1050 });
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
  await openWebChat(page);
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toHaveValue("Keep this draft while I review the files.");
  await closeWebChatOverlay(page);
  if (isMobile) await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await expect(page.getByRole("treeitem").filter({ hasText: "note.txt" })).toBeVisible();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.locator(".cm-content")).toHaveText("Draft survives layout changes");
  await page.getByRole("button", { name: "Collapse panel (tabs kept)", exact: true }).click();
  await expect.poll(() => reported).toMatchObject({ open_file: null, active_file: "docs/note.txt", left_open: !isMobile });
  await page.screenshot({ path: info.outputPath("workspace-conversation.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await openWebChat(page);
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("");
  await expect.poll(() => reported).toMatchObject({ has_draft: false });
  await closeWebChatOverlay(page);
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect.poll(() => reported).toMatchObject({ page: "/dashboard", left_open: false, dock_open: false, open_file: null });
});

test("touch input keeps Enter as a newline and stays above the virtual keyboard", async ({ page, isMobile }, info) => {
  test.skip(!isMobile, "Touch input");
  let sends = 0;
  await page.route("**/api/chat/send", (route) => { sends++; return route.fulfill({ json: { ok: true } }); });
  await page.goto("/webui/");
  await openWebChat(page);
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
  const sidebar = page.locator(".app-sidebar");
  await expect(sidebar.getByRole("textbox")).toHaveCount(0);
  const contextLink = sidebar.getByRole("link", { name: "Context", exact: true });
  await expect(contextLink).toBeVisible();
  await sidebar.getByRole("button", { name: "Pin Context", exact: true }).press("Enter");
  await expect(sidebar.getByRole("link", { name: "Context", exact: true })).toHaveCount(1);
  await expect(sidebar.getByRole("region", { name: "Favorites", exact: true }).getByRole("link", { name: "Context", exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "Unpin Context", exact: true })).toBeFocused();
  await sidebar.getByRole("button", { name: "Unpin Context", exact: true }).press("Enter");
  await expect(sidebar.getByRole("link", { name: "Context", exact: true })).toHaveCount(1);
  await expect(sidebar.getByRole("button", { name: "Pin Context", exact: true })).toBeFocused();
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
  await openWebChat(page);
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

test("management dialogs and popovers finish exit motion and respect reduced motion", async ({ page }) => {
  await page.goto("/webui/tasks");
  const edit = page.getByRole("button", { name: "Edit", exact: true });
  await edit.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toHaveCSS("animation-name", "surface-enter");
  const closing = await dialog.getByRole("button", { name: "Cancel", exact: true }).evaluate((button) => {
    (button as HTMLButtonElement).click();
    return new Promise((resolve) => queueMicrotask(() => {
      const surface = button.closest('[role="dialog"]');
      resolve({ connected: surface?.isConnected, state: surface?.getAttribute("data-state"), animation: surface && getComputedStyle(surface).animationName });
    }));
  });
  expect(closing).toEqual({ connected: true, state: "closed", animation: "surface-exit" });
  await expect(dialog).toHaveCount(0);
  await expect(edit).toBeFocused();
  await edit.click();
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  const appearance = page.getByRole("button", { name: "Appearance & language", exact: true });
  await appearance.click();
  const menu = page.locator(".appearance-menu");
  await expect(menu).toHaveCSS("animation-name", "surface-enter");
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(appearance).toBeFocused();

  await page.emulateMedia({ reducedMotion: "reduce" });
  await appearance.click();
  await expect(menu).toHaveCSS("animation-name", "none");
  await page.keyboard.press("Escape");
  await edit.click();
  await expect(dialog).toHaveCSS("animation-name", "none");
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(edit).toBeFocused();
});
