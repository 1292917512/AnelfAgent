import { test as base, type Page } from "@playwright/test";

export const persona = { name: "Daily assistant", description: "General purpose", personality: ["Patient"] };
export const task = {
  name: "daily-review", display_name: "Daily review", description: "Summarize recent work",
  folder: "personal", enabled: true, scope: "global", memory_type: "semantic", importance: 0.5,
  tags: [], source: "", null_keywords: [], tool_tags: [], prompt: "Review recent work", model_id: null,
};
export const metadata = { groups: [{ group: "adapter/telegram", items: [
  { key: "telegram_bot_token", type: "password", description: "Bot token", value: "abcd****5678", default: "", editable: true, options: [] },
  { key: "telegram_proxy_mode", type: "enum", description: "Network policy", value: "system", default: "system", editable: true, options: ["system", "direct", "proxy"], related_key: "proxy_enabled" },
] }] };

export async function mockApi(page: Page) {
  await page.addInitScript(() => { localStorage.setItem("i18nextLng", "en"); localStorage.setItem("theme", "light"); });
  await page.route("**/api/**", async (route) => {
    if (!new URL(route.request().url()).pathname.startsWith("/api/")) { await route.continue(); return; }
    const path = new URL(route.request().url()).pathname.replace(/^\/api/, "").replace(/\/$/, "");
    const responses: Record<string, unknown> = {
      "/auth/check": { required: false, authenticated: true },
      "/config/webui": { branding: { title: "AnelfAgent", version: "0.3.0" } },
      "/status": { ready: true, status: { status: "running", mind_phase: "idle", uptime: 3600, queue_size: 0 } },
      "/status/thinking": { pending_messages: [], general_tasks: [], pending_analysis_count: 0 },
      "/personas": [{ key: "daily", ...persona }, { key: "research", name: "Research assistant", description: "Research and analysis" }],
      "/personas/active": { active: "daily" },
      "/personas/daily": persona,
      "/personas/research": { name: "Research assistant", personality: ["Precise"] },
      "/config/tasks": [task],
      "/config/tasks/daily-review/history": [],
      "/config/meta": metadata,
      "/adapters": { ready: true, adapters: [{ key: "telegram", name: "Telegram", status: "stopped", status_display: "Stopped", detail: "" }] },
      "/models/priorities": {},
      "/provider-keys": { providers: [] },
      "/models/providers": [],
      "/judgment/providers": { providers: {}, default_model: "", fallback_models: [], revision: "0", health: {} },
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
      "/workspace/activity": { epoch: "test", revision: 0, runs: [] },
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

/** 可控浏览器事件流，沿用真实前端解码与恢复链路。 */
export async function installEventStream(page: Page) {
  await page.addInitScript(() => {
    let connections = 0;
    class TestEventSource extends EventTarget {
      static CONNECTING = 0; static OPEN = 1; static CLOSED = 2;
      readyState = 0;
      onopen: ((event: Event) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      deliver = (event: Event) => {
        const frame = (event as CustomEvent<{ event: string; data: unknown }>).detail;
        if (frame.event === "test-disconnect") {
          this.readyState = TestEventSource.CLOSED;
          this.onerror?.(new Event("error"));
          return;
        }
        this.dispatchEvent(new MessageEvent(frame.event, { data: JSON.stringify(frame.data) }));
      };
      constructor(url: string) {
        super();
        if (url.includes("/chat/stream")) {
          window.addEventListener("test-activity", this.deliver);
          Object.assign(window, { testStreamConnections: ++connections });
        }
        setTimeout(() => { this.readyState = 1; this.onopen?.(new Event("open")); }, 0);
      }
      close() { this.readyState = 2; window.removeEventListener("test-activity", this.deliver); }
    }
    Object.defineProperty(window, "EventSource", { value: TestEventSource });
  });
}
