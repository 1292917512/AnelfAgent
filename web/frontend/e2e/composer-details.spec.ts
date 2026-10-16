import { test, expect, openWebChat, closeWebChatOverlay } from "./fixtures";

test("uploaded chat images load after sending and remain accessible from history", async ({ page }, testInfo) => {
  const name = "screen (1) #预算.png";
  const filename = `123_${name}`;
  const url = `/api/chat/files/image/${encodeURIComponent(filename).replace(/[()]/g, (char) => `%${char.charCodeAt(0).toString(16)}`)}`;
  const image = Buffer.from(await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 240; canvas.height = 80;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#dbeafe"; ctx.fillRect(0, 0, 240, 80);
    ctx.fillStyle = "#1e40af"; ctx.font = "20px sans-serif"; ctx.fillText("Chat image preview", 20, 48);
    return canvas.toDataURL("image/png").split(",")[1]!;
  }), "base64");
  let messageId = "";
  await page.route("**/api/chat/upload", (route) => route.fulfill({ json: {
    path: `/workspace/uploads/image/${filename}`, url, name, type: "image", size: image.length,
  } }));
  await page.route("**/api/chat/files/image/**", (route) => route.fulfill({ contentType: "image/png", body: image }));
  await page.route("**/api/chat/send", (route) => {
    const data = route.request().postDataJSON() as { message: string; message_id: string; files: string[] };
    messageId = data.message_id;
    expect(data.message).toBe("Check this image");
    expect(data.files).toEqual([`/workspace/uploads/image/${filename}`]);
    return route.fulfill({ json: { ok: true } });
  });
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: messageId ? [{
    id: 1, cid: messageId, role: "user", ts: 1,
    content: `Check this image\n[${filename}](./uploads/image/${encodeURIComponent(filename)})`,
  }] : [] }));
  await page.route("**/api/workspace/file*", (route) => route.fulfill({ json: {
    path: `uploads/image/${filename}`, name: filename, binary: true, type: "image", size: image.length,
  } }));
  await page.route("**/api/workspace/raw*", (route) => route.fulfill({ contentType: "image/png", body: image }));
  await page.goto("/webui/");
  await openWebChat(page);
  await page.locator('.composer-card input[type="file"]').setInputFiles({ name, mimeType: "image/png", buffer: image });
  await expect(page.getByRole("list", { name: "Pending attachments" })).not.toContainText("Uploading");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Check this image");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const sentImage = page.locator(".message-bubble img");
  await expect(sentImage).toHaveCount(1);
  await expect.poll(() => sentImage.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(240);
  await expect(sentImage).toHaveAttribute("src", url);
  await page.screenshot({ path: testInfo.outputPath("chat-image.png"), animations: "disabled" });
  await sentImage.click();
  const lightbox = page.getByRole("dialog", { name: "Image preview", exact: true });
  await expect.poll(() => lightbox.getByRole("img", { name: "image", exact: true }).evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(240);
  await lightbox.getByRole("button", { name: "Zoom in", exact: true }).click();
  await expect(lightbox.getByRole("button", { name: "Zoom out", exact: true })).toBeVisible();
  await lightbox.getByRole("button", { name: "Close", exact: true }).click();
  await expect(lightbox).not.toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();
  await sentImage.click();
  await page.keyboard.press("Escape");
  await expect(lightbox).not.toBeVisible();
  await expect(page.getByRole("textbox", { name: "Message", exact: true })).toBeVisible();
  await page.reload();
  await openWebChat(page);
  await page.getByRole("button", { name: `${filename} Workspace`, exact: true }).click();
  await expect.poll(() => page.getByRole("img", { name: filename, exact: true }).evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(240);
  await page.screenshot({ path: testInfo.outputPath("chat-image-history.png"), animations: "disabled" });
});

test("attachment tray stays inside the composer and keeps long filenames contained", async ({ page }, testInfo) => {
  await page.route("**/api/chat/upload", (route) => route.fulfill({ json: { path: "/uploads/example.txt", type: "file" } }));
  await page.goto("/webui/");
  await openWebChat(page);
  const card = page.locator(".composer-card");
  await card.locator('input[type="file"]').setInputFiles(Array.from({ length: 4 }, (_, index) => ({
    name: `a-very-long-workspace-reference-name-${index}.txt`, mimeType: "text/plain", buffer: Buffer.from("test"),
  })));
  const tray = card.getByRole("list", { name: "Pending attachments" });
  await expect(tray.getByRole("listitem")).toHaveCount(4);
  await expect(tray.getByText("Attachment", { exact: true })).toHaveCount(4);
  const input = card.getByRole("textbox");
  const trayBox = await tray.boundingBox();
  const inputBox = await input.boundingBox();
  expect(trayBox).not.toBeNull();
  expect(inputBox).not.toBeNull();
  expect(inputBox!.y - (trayBox!.y + trayBox!.height)).toBeLessThanOrEqual(4);
  expect(await tray.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
  await tray.getByRole("button", { name: /Remove attachment/ }).first().click();
  await expect(tray.getByRole("listitem")).toHaveCount(3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("attachments.png") });
});

test("command search has one integrated focus treatment and pointer selection remains enabled", async ({ page }, testInfo) => {
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Command Palette", exact: true }).click();
  const dialog = page.locator(".command-palette");
  const input = dialog.getByRole("combobox");
  await expect(input).toBeFocused();
  expect(await input.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe("none");
  expect(await input.evaluate((el) => getComputedStyle(el).boxShadow)).toBe("none");
  const option = dialog.getByRole("option").filter({ hasText: "Overview" });
  await expect(option).toHaveCSS("pointer-events", "auto");
  await page.screenshot({ path: testInfo.outputPath("palette.png") });
  await option.click();
  await expect(page).toHaveURL(/\/dashboard/);
  await expect(dialog).not.toBeVisible();
});

test("external file drops show feedback and become composer attachments", async ({ page }) => {
  await page.route("**/api/chat/upload", (route) => route.fulfill({ json: { path: "/uploads/dropped.txt" } }));
  await page.goto("/webui/");
  await openWebChat(page);
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  const transfer = await page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(new File(["content"], "dropped.txt", { type: "text/plain" }));
    return data;
  });
  await input.dispatchEvent("dragenter", { dataTransfer: transfer });
  await expect(page.getByText("Drop to attach file", { exact: true })).toBeVisible();
  await input.dispatchEvent("dragover", { dataTransfer: transfer });
  await input.dispatchEvent("drop", { dataTransfer: transfer });
  await expect(page.locator(".composer-attachment")).toContainText("dropped.txt");
  await expect(page.locator(".composer-attachment")).toContainText("Attachment");
  await expect(page.getByText("Drop to attach file", { exact: true })).not.toBeVisible();
  await transfer.dispose();
});

test("workspace directories keep their identity when attached", async ({ page, isMobile }, testInfo) => {
  await page.route("**/api/workspace/tree**", (route) => route.fulfill({ json: {
    path: "", children: ["plugins", "pybox", "mengli_aigc"].map((name) => ({ name, path: name, type: "dir" })), truncated: false,
  } }));
  await page.goto("/webui/");
  await page.getByRole("button", { name: "Workspace files", exact: true }).click();
  for (const name of ["plugins", "pybox", "mengli_aigc"]) {
    const node = page.getByRole("treeitem").filter({ hasText: name });
    if (isMobile) await node.getByRole("button", { name: "more", exact: true }).click();
    else await node.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Attach to chat", exact: true }).click();
  }
  const selected = page.getByRole("treeitem").filter({ hasText: "plugins" });
  if (isMobile) await selected.getByRole("button", { name: "more", exact: true }).click();
  else await selected.click({ button: "right" });
  await expect(page.getByRole("menuitem", { name: "Attach to chat", exact: true })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("menuitem", { name: "New file", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("menu")).not.toBeVisible();
  if (isMobile) await page.getByRole("dialog").getByRole("button", { name: "Close", exact: true }).click();
  await openWebChat(page);
  const tray = page.locator(".composer-attachments");
  await expect(tray.getByRole("listitem")).toHaveCount(3);
  await expect(tray).toContainText("Workspace");
  await expect(tray).toContainText("Folder");
  await page.screenshot({ path: testInfo.outputPath("directories.png") });
  await closeWebChatOverlay(page);
  await page.getByRole("button", { name: "Appearance & language", exact: true }).click();
  await page.getByRole("button", { name: /Switch to dark|Dark theme|Use dark|dark mode/i }).click();
  await page.keyboard.press("Escape");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await openWebChat(page);
  await expect(page.locator(".composer-card")).toHaveCSS("background-color", "rgb(23, 30, 43)");
  await page.screenshot({ path: testInfo.outputPath("directories-dark.png"), animations: "disabled" });
});
