import { test, expect, persona, installEventStream } from "./fixtures";

test("AI questions align actions, preserve drafts on failure and animate between queued questions", async ({ page }, info) => {
  await installEventStream(page);
  let fail = true;
  const answers: string[] = [];
  await page.route("**/api/chat/ui-answer", (route) => {
    answers.push(route.request().postDataJSON().answer);
    return route.fulfill(fail ? { status: 503, json: { detail: "Try again" } } : { json: { status: "ok" } });
  });
  await page.goto("/webui/");
  await expect(page.getByText("No execution records yet", { exact: true })).toBeVisible();
  await page.evaluate(() => {
    const animations: string[] = [];
    Reflect.set(window, "askAnimations", animations);
    document.addEventListener("animationstart", (event) => {
      if (event.target instanceof Element && event.target.matches(".dialog-surface")) animations.push(event.animationName);
    });
  });
  const question = { ask_id: "layout-check", question: "Which changes should I verify before continuing?", options: ["Check layout, button alignment and transitions on desktop and mobile", "Review the changes first"], ts: Date.now() / 1000, expires_at: Date.now() / 1000 + 600 };
  await page.evaluate((data) => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "ui_command", data: { command: "ask", ...data } } })), question);
  const dialog = page.getByRole("dialog", { name: "AI needs your input" });
  await expect(dialog).toBeVisible();
  const submit = dialog.getByRole("button", { name: "Submit", exact: true });
  const skip = dialog.getByRole("button", { name: "Skip", exact: true });
  await expect(submit).toBeDisabled();
  await dialog.getByText(question.options[0]!, { exact: true }).click();
  await expect(dialog.getByRole("radio").first()).toBeChecked();
  expect(answers).toHaveLength(0);
  const submitBox = await submit.boundingBox();
  const skipBox = await skip.boundingBox();
  expect(submitBox).not.toBeNull(); expect(skipBox).not.toBeNull();
  expect(Math.abs(submitBox!.y - skipBox!.y)).toBeLessThan(1);
  expect(Math.abs(submitBox!.height - skipBox!.height)).toBeLessThan(1);
  const dialogBox = await dialog.boundingBox();
  expect(dialogBox!.y + dialogBox!.height - submitBox!.y - submitBox!.height).toBeGreaterThanOrEqual(16);
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("ai-question-light.png"), animations: "disabled" });
  await dialog.getByRole("textbox").fill("Verify both screen sizes\nKeep my draft on failure");
  await submit.click();
  await expect(dialog.getByRole("alert")).toContainText("Try again");
  await expect(dialog.getByRole("textbox")).toHaveValue("Verify both screen sizes\nKeep my draft on failure");
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
    document.documentElement.classList.add("dark");
  });
  await page.screenshot({ path: info.outputPath("ai-question-dark.png"), animations: "disabled" });
  await page.evaluate((data) => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event: "ui_command", data: { command: "ask", ...data, ask_id: "next", question: "Second question" } } })), question);
  fail = false;
  await submit.click();
  await expect(dialog.getByText("Second question", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("textbox")).toHaveValue("");
  await expect(submit).toBeDisabled();
  await skip.click();
  await expect(dialog).toHaveCount(0);
  expect(await page.evaluate(() => Reflect.get(window, "askAnimations"))).toEqual(expect.arrayContaining(["surface-enter", "surface-exit"]));
  expect(answers).toEqual(["Verify both screen sizes\nKeep my draft on failure", "Verify both screen sizes\nKeep my draft on failure", "__skipped__"]);
});

test("persona drafts stay with their owner, accept incomplete JSON, and guard navigation", async ({ page }) => {
  await page.goto("/webui/personas?persona=daily");
  const name = page.getByRole("textbox", { name: "Name", exact: true });
  await expect(name).toHaveValue(persona.name);
  await name.fill("My assistant");
  const personality = page.getByRole("textbox", { name: /Personality/ });
  await personality.fill('["Calm",');
  await expect(personality).toHaveValue('["Calm",');
  await page.getByRole("button", { name: /Research assistant Research/ }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(name).toHaveValue("My assistant");
  await personality.fill('["Calm", "Precise"]');
  const request = page.waitForRequest((req) => req.method() === "PUT" && req.url().endsWith("/personas/daily"));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ name: "My assistant", personality: ["Calm", "Precise"] });
  await expect(page.getByText("Saved", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Research assistant Research/ }).click();
  await expect(name).toHaveValue("Research assistant");
});

test("tasks share a complete editor with usable tag entry and zero importance", async ({ page }) => {
  await page.goto("/webui/tasks");
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("spinbutton").fill("0");
  const tags = dialog.locator("fieldset").filter({ has: page.locator("legend", { hasText: /^Tags/ }) }).last().getByRole("textbox");
  await tags.fill("alpha, beta");
  await expect(tags).toHaveValue("alpha, beta");
  const request = page.waitForRequest((req) => req.method() === "PUT" && req.url().includes("/config/tasks/daily-review"));
  await dialog.getByRole("button", { name: "Save", exact: true }).click();
  expect((await request).postDataJSON()).toMatchObject({ importance: 0, tags: ["alpha", "beta"], folder: "personal" });
  await expect(dialog).not.toBeVisible();
});

test("channel partial saves retain failed fields and changes made during saving", async ({ page }) => {
  let release: (() => void) | undefined;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/api/config/meta/telegram_bot_token", async (route) => {
    await wait;
    await route.fulfill({ json: { status: "ok", key: "telegram_bot_token", value: "new-****cret" } });
  });
  await page.route("**/api/config/meta/telegram_proxy_mode", (route) => route.fulfill({ status: 400, json: { detail: "Invalid proxy" } }));
  await page.goto("/webui/channels");
  await page.getByRole("button", { name: /Telegram.*Stopped/ }).click();
  const tokenInput = page.getByLabel("Bot token", { exact: true });
  const proxyInput = page.getByLabel("Network policy", { exact: true });
  await tokenInput.fill("new-secret");
  await proxyInput.selectOption("proxy");
  await expect(page.getByRole("link", { name: "Related setting" })).toHaveAttribute("href", "/webui/config?key=proxy_enabled");
  await page.getByRole("button", { name: /Save Config|Save config|Save configuration/ }).click();
  await tokenInput.fill("newer-secret");
  release?.();
  await expect(page.getByText("Invalid proxy")).toBeVisible();
  await expect(tokenInput).toHaveValue("newer-secret");
  await expect(proxyInput).toHaveValue("proxy");
  await expect(page.getByRole("button", { name: /Save Config|Save config|Save configuration/ })).toContainText("(2)");
});

test("tabs preserve deep links and browser history", async ({ page }) => {
  await page.goto("/webui/channels?ref=bookmark");
  await page.getByRole("tab").nth(1).click();
  await expect(page).toHaveURL(/ref=bookmark&tab=test/);
  await page.reload();
  await expect(page.getByRole("tab").nth(1)).toHaveAttribute("aria-selected", "true");
  await page.goBack();
  await expect(page.getByRole("tab").first()).toHaveAttribute("aria-selected", "true");
});

test("notes keep unsaved content when another file is selected", async ({ page }) => {
  await page.goto("/webui/memory?tab=notes");
  const editor = page.getByRole("textbox", { name: "memory/memory.md", exact: true });
  await editor.fill("Unsaved note");
  await page.getByRole("button", { name: /ideas.md/ }).click();
  await page.getByRole("button", { name: "Keep editing", exact: true }).click();
  await expect(editor).toHaveValue("Unsaved note");
});

test("navigation exposes all core areas and mobile pages stay within the viewport", async ({ page, isMobile }) => {
  await page.goto("/webui/tasks");
  if (isMobile) await page.getByRole("button", { name: "More", exact: true }).click();
  else await page.getByRole("button", { name: "Expand sidebar", exact: true }).click();
  const navigation = page.getByRole("navigation").first();
  await expect(navigation.getByRole("link", { name: /Persona/i })).toBeVisible();
  await expect(navigation.getByRole("link", { name: /Config/ })).toBeAttached();
  expect(await navigation.getByRole("link").count()).toBeGreaterThanOrEqual(23);
  if (isMobile) {
    await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});

test("authentication outages show a recoverable error and never mount private pages", async ({ page }) => {
  await page.route("**/api/auth/check", (route) => route.fulfill({ status: 503, json: { detail: "Offline" } }));
  await page.goto("/webui/tasks");
  await expect(page.getByText("Offline", { exact: true })).toBeVisible();
  await expect(page.getByRole("navigation")).toHaveCount(0);
  await page.route("**/api/auth/check", (route) => route.fulfill({ json: { required: false, authenticated: true } }));
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Tasks", exact: true })).toBeVisible();
});


test("file drafts survive panel collapse and stay isolated between roots", async ({ page, isMobile }) => {
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  const editor = page.locator(".cm-content");
  await expect(editor).toHaveText("workspace content");
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText("Unsaved workspace draft");
  await expect(editor).toHaveText("Unsaved workspace draft");
  await page.getByRole("button", { name: "Collapse panel (tabs kept)", exact: true }).click();
  if (isMobile) await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("button", { name: "Project", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(editor).toHaveText("project content");
  await editor.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.insertText("Project draft");
  await expect(editor).toHaveText("Project draft");
  await page.getByRole("button", { name: "note.txt", exact: true }).click();
  await expect(editor).toHaveText("Unsaved workspace draft");
  const request = page.waitForRequest((req) => req.method() === "PUT" && req.url().endsWith("/workspace/file"));
  await page.getByRole("button", { name: "Save", exact: true }).click();
  expect((await request).postDataJSON()).toEqual({ path: "note.txt", root: "workspace", content: "Unsaved workspace draft", expected_version: "v1" });
  await page.getByRole("button", { name: "project:note.txt", exact: true }).click();
  await expect(editor).toHaveText("Project draft");
});

test("failed workspace file reads can be retried without reopening the tab", async ({ page }) => {
  let failing = true;
  await page.route("**/api/workspace/file?*", (route) => failing
    ? route.fulfill({ status: 503, json: { detail: "File storage unavailable" } })
    : route.fallback());
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  await page.getByRole("treeitem").filter({ hasText: "note.txt" }).click();
  await expect(page.getByRole("alert")).toContainText("File storage unavailable");
  failing = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.locator(".cm-content")).toHaveText("workspace content");
});
