import { test, expect, installEventStream, openWebChat } from "./fixtures";
import type { ActivityRun } from "../src/lib/types/activity";

/** Opt-in real-time soak: production build, isolated API data, Chromium heap and paint probes. */
test("workspace sustained traffic retains bounded memory and delivers the newest messages", async ({ page, context }, info) => {
  test.skip(!process.env.FRONTEND_SOAK || info.project.name !== "desktop", "Opt-in Chromium performance measurement");
  const seconds = Number(process.env.FRONTEND_SOAK_SECONDS ?? 180);
  test.setTimeout((seconds + 120) * 1000);
  await installEventStream(page);
  await page.addInitScript(() => {
    const tasks: number[] = [];
    new PerformanceObserver((list) => { for (const entry of list.getEntries()) tasks.push(entry.duration); }).observe({ type: "longtask" });
    Object.assign(window, { soakLongTasks: tasks });
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const history = Array.from({ length: 2000 }, (_, i) => ({ id: i + 1, role: "assistant", ts: i + 1,
    content: `Message ${i + 1}\n\n**Result:** ${"Historical context with useful detail. ".repeat(12)}` }));
  let historyRequests = 0;
  await page.route("**/api/chat/history*", (route) => {
    historyRequests++;
    const params = new URL(route.request().url()).searchParams;
    const limit = Number(params.get("limit"));
    const before = Number(params.get("before_id") ?? Infinity);
    const after = params.has("after_id") ? Number(params.get("after_id")) : null;
    return route.fulfill({ json: after == null ? history.filter((m) => m.id < before).slice(-limit)
      : history.filter((m) => m.id > after).slice(0, limit) });
  });
  const run: ActivityRun = { id: "soak", revision: 1, kind: "conversation", scope: "", origin_scope: "", parent_id: "", owner_id: "", actor: "", label: "Sustained execution", input: "",
    source: { channel: "webui", kind: "user", target: "web_user", session: "default", scope: "" }, status: "running",
    started_at: 1, updated_at: 1, ended_at: null, entry_count: 120, truncated: false,
    entries: Array.from({ length: 120 }, (_, i) => ({ id: `step-${i}`, kind: "text", ts: i + 1, content: `Step ${i}\n\n${"Completed investigation. ".repeat(20)}` })),
  };
  await page.route(/\/api\/workspace\/activity(?:\?.*)?$/, (route) => route.fulfill({ json: {
    epoch: "soak", revision: run.revision, runs: new URL(route.request().url()).searchParams.get("since_revision") === String(run.revision) ? [] : [run], retained_ids: [run.id],
  } }));
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");
  const samples: Record<string, number | string>[] = [];
  const sample = async (label: string) => {
    await cdp.send("HeapProfiler.collectGarbage");
    const { usedSize } = await cdp.send("Runtime.getHeapUsage");
    const counters = await cdp.send("Memory.getDOMCounters");
    const metrics = await cdp.send("Performance.getMetrics");
    samples.push({ label, heapMiB: usedSize / 1024 / 1024, nodes: counters.nodes, listeners: counters.jsEventListeners,
      taskSeconds: metrics.metrics.find((m) => m.name === "TaskDuration")?.value ?? 0,
      messages: await page.locator(".message-list [data-message-key]").count(), historyRequests });
    console.log("SOAK_SAMPLE", JSON.stringify(samples[samples.length - 1]));
  };
  const navigationStart = Date.now();
  await page.goto("/webui/");
  await openWebChat(page);
  await expect(page.locator(".message-list [data-message-key]")).toHaveCount(40);
  const initialLoadMs = Date.now() - navigationStart;
  await sample("initial");
  const delays: number[] = [];
  const dispatch: number[] = [];
  const start = Date.now();
  let ticks = 0;
  while (Date.now() - start < seconds * 1000) {
    const tickStart = Date.now();
    const messages = Array.from({ length: 5 }, (_, i) => {
      const id = history.length + i + 1;
      return { id, role: "assistant", ts: Date.now() / 1000, content: `Live message ${id}\n\n${"Response content. ".repeat(20)}` };
    });
    history.push(...messages);
    run.revision++;
    run.entries[119] = { id: "step-119", kind: "text", ts: 120, content: `Live activity ${ticks}\n\n${"Progress detail. ".repeat(300)}` };
    const result = await page.evaluate(async ({ messages: batch, activity, tick }) => {
      const start = performance.now();
      const emit = (event: string, data: unknown) => window.dispatchEvent(new CustomEvent("test-activity", { detail: { event, data } }));
      for (let i = 0; i < 40; i++) emit("file_diff", { path: `generated/part-${tick * 40 + i}.ts`, diff: "+change", additions: 1, removals: 0 });
      for (const message of batch) emit("reply", { content: message.content });
      emit("activity", { epoch: "soak", run: activity });
      emit("turn_end", {});
      const dispatchMs = performance.now() - start;
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      return { dispatchMs, paintMs: performance.now() - start };
    }, { messages, activity: run, tick: ticks });
    delays.push(result.paintMs); dispatch.push(result.dispatchMs);
    await expect(page.locator(".message-list")).toContainText(`Live message ${history.length}`);
    ticks++;
    if (ticks % 60 === 0) await sample(`tick-${ticks}`);
    await page.waitForTimeout(Math.max(0, 500 - (Date.now() - tickStart)));
  }
  await expect(page.locator(".message-list [data-message-key]")).toHaveCount(120);
  await sample("settled");
  const trafficTasks = await page.evaluate(() => Reflect.get(window, "soakLongTasks") as number[]);
  const readingStart = Date.now();
  for (let i = 0; i < 20; i++) {
    await page.getByRole("button", { name: "Load earlier messages", exact: true }).click();
    await expect(page.locator(".message-list [data-message-key]")).toHaveCount(120 + (i + 1) * 40);
  }
  const readingMs = Date.now() - readingStart;
  await sample("reading-920");
  await page.getByRole("button", { name: "Back to latest messages", exact: true }).click();
  await expect(page.locator(".message-list [data-message-key]")).toHaveCount(40);
  await sample("reading-released");
  const refreshStart = Date.now();
  await page.reload();
  await openWebChat(page);
  await expect(page.locator(".message-list [data-message-key]")).toHaveCount(40);
  await expect(page.locator(".message-list")).toContainText(`Live message ${history.length}`);
  const refreshMs = Date.now() - refreshStart;
  await sample("reloaded");
  const tasks = await page.evaluate(() => Reflect.get(window, "soakLongTasks") as number[]);
  const percentile = (items: number[], fraction: number) => [...items].sort((a, b) => a - b)[Math.floor((items.length - 1) * fraction)] ?? 0;
  const report = { seconds, ticks, messages: ticks * 5, fileEvents: ticks * 40, initialLoadMs, refreshMs, readingMs,
    paintP50: percentile(delays, .5), paintP95: percentile(delays, .95), paintMax: Math.max(...delays),
    dispatchP95: percentile(dispatch, .95), longTasks: trafficTasks.length, longTaskMax: Math.max(0, ...trafficTasks),
    longTasksAfterReload: tasks.length, samples, errors };
  console.log("SOAK_REPORT", JSON.stringify(report));
  await info.attach("soak-metrics", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
  await page.screenshot({ path: info.outputPath("soak-final.png") });
  expect(errors).toEqual([]);
});
