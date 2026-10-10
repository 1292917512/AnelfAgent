import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";
import type { ContextSnapshotData } from "../src/lib/types";

const snapshot: ContextSnapshotData = {
  captured_at: 1770000000, model: "research-model", model_context_window: 128000,
  estimated_tokens: 50000, message_count: 35, tool_count: 2, tool_names: ["read_file", "search"], tools: [],
  sections: [
    { layer: "stable", label: "Stable instructions", count: 1, chars: 12000, estimated_tokens: 4000, stable_count: 1, volatility_label: "Static", messages: [{ role: "system", content: "Stable instructions" }] },
    { layer: "conversation", label: "Conversation", count: 30, chars: 100000, estimated_tokens: 40000, stable_count: 28, volatility_label: "Append only", messages: [{ role: "user", content: "Review the workspace" }] },
    { layer: "provider", label: "Live workspace", count: 1, chars: 3000, estimated_tokens: 1000, stable_count: 0, volatility_label: "Every round", messages: [{ role: "system", content: "Current workspace state" }] },
  ],
};

test("context is independent, shows window composition and follows server capture state", async ({ page }, info) => {
  let armed = false;
  await page.route("**/api/context/snapshot", (route) => route.fulfill({ json: { status: { armed, continuous: false }, snapshot } }));
  await page.route("**/api/context/snapshot/arm", (route) => { armed = true; return route.fulfill({ json: { armed } }); });
  await page.goto("/webui/context");
  const window = page.getByRole("region", { name: "Context window" });
  await expect(window).toContainText("50,000");
  await expect(window).toContainText("78,000 available");
  await window.getByRole("button", { name: /Stable instructions/ }).click();
  await expect(window.getByRole("status")).toContainText("1 unchanged messages");
  await page.getByRole("button", { name: "Start Capture", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Session List", exact: true })).toHaveCount(0);
  const axe = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((violation) => ({ id: violation.id, nodes: violation.nodes.map((node) => ({ target: node.target, summary: node.failureSummary })) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("context.png") });
});

test("a late history response cannot replace the newly selected snapshot", async ({ page }) => {
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/context/snapshots", (route) => route.fulfill({ json: { snapshots: [
    { ...snapshot, filename: "first.json", model: "first-model" },
    { ...snapshot, filename: "second.json", model: "second-model" },
  ] } }));
  await page.route("**/api/context/snapshots/first.json", async (route) => { await waiting; await route.fulfill({ json: { ...snapshot, model: "first-model" } }); });
  await page.route("**/api/context/snapshots/second.json", (route) => route.fulfill({ json: { ...snapshot, model: "second-model" } }));
  await page.goto("/webui/context?tab=history");
  await page.getByRole("button").filter({ hasText: "first-model" }).click();
  await page.getByRole("button", { name: /Back to list/ }).click();
  await page.getByRole("button").filter({ hasText: "second-model" }).click();
  await expect(page.getByText("second-model", { exact: true })).toBeVisible();
  release();
  await expect(page.getByText("first-model", { exact: true })).toHaveCount(0);
  await expect(page.getByText("second-model", { exact: true })).toBeVisible();
});

test("file references stay inline and directory clicks select the project root", async ({ page }, info) => {
  await page.route("**/api/workspace/tree?**", (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    return route.fulfill({ json: { path, children: path === "docs"
      ? [{ name: "guide.md", path: "docs/guide.md", type: "file", size: 100, modified: 0 }]
      : [{ name: "docs", path: "docs", type: "dir", has_children: true, modified: 0 }], truncated: false } });
  });
  await page.route("**/api/chat/history?**", (route) => route.fulfill({ json: [{ id: 1, role: "user", content: "Please review **these files**:\n\n- [Project docs](./project%3Adir%3Adocs) with notes\n- [Draft \\[1\\]](./note%20%281%29.txt)\n\n`[literal](./note.txt)`", ts: 1770000000 }] }));
  await page.goto("/webui/");
  const ref = page.getByRole("button", { name: /Project docs/ });
  await expect(ref).toBeVisible();
  await expect(ref.locator("..")).toContainText("with notes");
  await expect(page.getByRole("button", { name: /literal/ })).toHaveCount(0);
  await ref.click();
  await expect(page.getByRole("button", { name: "Project", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Project", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("treeitem").filter({ hasText: "guide.md" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("workspace.png") });
});

test("dark context remains readable on a narrow viewport", async ({ page }, info) => {
  await page.addInitScript(() => localStorage.setItem("theme", "dark"));
  if (info.project.name === "desktop") await page.setViewportSize({ width: 900, height: 900 });
  await page.route("**/api/context/snapshot", (route) => route.fulfill({ json: { status: { armed: false, continuous: false }, snapshot } }));
  await page.goto("/webui/context");
  await expect(page.getByRole("region", { name: "Context window" })).toBeVisible();
  const axe = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((violation) => ({ id: violation.id, nodes: violation.nodes.map((node) => node.failureSummary) }))).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("context-dark.png") });
});

test("desktop navigation expands without moving the workspace and supports keyboard focus", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Hover navigation applies to desktop pointers");
  await page.goto("/webui/");
  const sidebar = page.locator(".app-sidebar");
  await expect(sidebar).toHaveCSS("width", "68px");
  const bounds = await page.getByRole("main").boundingBox();
  await sidebar.hover();
  await expect(sidebar).toHaveCSS("width", "248px");
  expect(await page.getByRole("main").boundingBox()).toEqual(bounds);
  await page.getByRole("main").hover();
  await expect(sidebar).toHaveCSS("width", "68px");
  await page.locator(".skip-link").focus();
  await page.keyboard.press("Tab");
  await expect(sidebar).toHaveCSS("width", "248px");
  await expect(sidebar.getByRole("button", { name: "Search", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(sidebar).toHaveCSS("width", "68px");
});

test("tablet workspace uses an editor overlay and keeps the conversation width", async ({ page }, info) => {
  test.skip(info.project.name !== "desktop", "Tablet layout uses a desktop pointer");
  await page.setViewportSize({ width: 900, height: 900 });
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  const files = page.getByRole("dialog", { name: "Workspace files", exact: true });
  await expect(files).toBeVisible();
  await files.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  const editor = page.getByRole("dialog", { name: "note.txt", exact: true });
  await expect(editor).toBeVisible();
  await expect(editor.locator(".cm-content")).toHaveText("workspace content");
  await page.screenshot({ path: info.outputPath("workspace-tablet.png") });
  await editor.getByRole("button", { name: "Collapse panel (tabs kept)" }).click();
  await files.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("snapshot outages stay inside the context panel and can be retried", async ({ page }) => {
  let failing = true;
  await page.route("**/api/context/snapshot", (route) => failing
    ? route.fulfill({ status: 503, json: { detail: "Snapshot unavailable" } })
    : route.fulfill({ json: { status: { armed: false, continuous: false }, snapshot } }));
  await page.goto("/webui/context");
  await expect(page.getByRole("alert")).toContainText("Snapshot unavailable");
  await expect(page.getByRole("heading", { name: "Context Management", exact: true })).toBeVisible();
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("region", { name: "Context window" })).toBeVisible();
});
