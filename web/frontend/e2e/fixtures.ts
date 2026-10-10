import { test as base, type Page } from "@playwright/test";

export const persona = { name: "Daily assistant", description: "General purpose", personality: ["Patient"] };
export const task = {
  name: "daily-review", display_name: "Daily review", description: "Summarize recent work",
  folder: "personal", enabled: true, scope: "global", memory_type: "semantic", importance: 0.5,
  tags: [], source: "", null_keywords: [], tool_tags: [], prompt: "Review recent work", model_id: null,
};
export const metadata = { groups: [{ group: "adapter/telegram", items: [
  { key: "telegram_bot_token", type: "password", description: "Bot token", value: "abcd****5678", default: "", editable: true, options: [] },
  { key: "telegram_proxy_url", type: "string", description: "Proxy URL", value: "", default: "", editable: true, options: [] },
] }] };

export async function mockApi(page: Page) {
  await page.addInitScript(() => { localStorage.setItem("i18nextLng", "en"); localStorage.setItem("theme", "light"); });
  await page.route("**/api/**", async (route) => {
    if (!new URL(route.request().url()).pathname.startsWith("/api/")) { await route.continue(); return; }
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "").replace(/\/$/, "");
    const responses: Record<string, unknown> = {
      "/auth/check": { required: false, authenticated: true },
      "/config/webui": { branding: { title: "AnelfAgent", version: "0.3.0" } },
      "/status": { ready: true, uptime_seconds: 3600 },
      "/personas": [{ key: "daily", ...persona }, { key: "research", name: "Research assistant", description: "Research and analysis" }],
      "/personas/active": { active: "daily" },
      "/personas/daily": persona,
      "/personas/research": { name: "Research assistant", personality: ["Precise"] },
      "/config/tasks": [task],
      "/config/tasks/daily-review/history": [],
      "/config/meta": metadata,
      "/adapters": { ready: true, adapters: [{ key: "telegram", name: "Telegram", status: "stopped", status_display: "Stopped", detail: "" }] },
      "/models/priorities": {},
      "/models/providers": [],
      "/memory/files": [{ path: "memory/ideas.md", lines: "2", size: "32 B" }],
      "/memory/notes": { content: "# Notes\nOriginal note", path: "memory/memory.md" },
      "/memory/files/content": { content: "Research notes" },
      "/status/logs": { logs: [], count: 0 },
      "/status/log-stats": { total: 0, by_level: {}, by_tag: {} },
      "/chat/bot-name": { name: "AnelfAgent" },
      "/chat/history": [],
      "/chat/chats": { chats: [] },
      "/chat/folds": { folds: [] },
      "/chat/delegations": { running: [] },
      "/delegations/overview": { running: [] },
      "/delegations/history": { items: [] },
      "/thinking/status": { enabled: false },
      "/thinking/sessions": { sessions: [], count: 0 },
      "/workspace/tree": { path: "", children: [{ name: "note.txt", path: "note.txt", type: "file", size: 8, modified: 0 }], truncated: false },
    };
    if (path === "/chat/stream") { await route.fulfill({ contentType: "text/event-stream", body: ": connected\n\n" }); return; }
    if (path === "/workspace/file" && route.request().method() === "GET") {
      const root = new URL(route.request().url()).searchParams.get("root");
      await route.fulfill({ json: { path: "note.txt", name: "note.txt", content: `${root} content`, version: "v1", size: 20, modified: 0, binary: false, truncated: false } });
      return;
    }
    if (path === "/workspace/file" && route.request().method() === "PUT") {
      const body = route.request().postDataJSON() as { path: string; content: string };
      await route.fulfill({ json: { ...body, name: body.path.split("/").pop(), version: "v2", size: body.content.length, modified: 0, binary: false, truncated: false } });
      return;
    }
    if (route.request().method() !== "GET") {
      await route.fulfill({ json: { status: "ok" } }); return;
    }
    if (Object.prototype.hasOwnProperty.call(responses, path)) await route.fulfill({ json: responses[path] });
    else await route.fulfill({ status: 503, json: { detail: "Test service unavailable" } });
  });
}

export const test = base.extend({
  page: async ({ page }, use) => { await mockApi(page); await use(page); },
});
export { expect } from "@playwright/test";


export async function openWebChat(page: Page) {
  await page.getByRole("button", { name: "Web chat", exact: true }).waitFor();
  if (!await page.getByRole("textbox", { name: "Message", exact: true }).isVisible()) {
    await page.getByRole("button", { name: "Web chat", exact: true }).click();
  }
}

export async function closeWebChatOverlay(page: Page) {
  const dialog = page.getByRole("dialog", { name: "Web chat", exact: true });
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: "Close", exact: true }).click();
}
