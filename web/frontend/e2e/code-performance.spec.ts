import { test, expect, installEventStream, openWebChat } from "./fixtures";

/** Production-only comparison of opening code-heavy history; no model/network latency. */
test("code-heavy history rendering metrics", async ({ page, context }, info) => {
  test.skip(!process.env.FRONTEND_RENDER_BENCH || info.project.name !== "desktop", "Opt-in Chromium render measurement");
  await installEventStream(page);
  await page.addInitScript(() => {
    const tasks: number[] = [];
    const frames: number[] = [];
    new PerformanceObserver((list) => { for (const entry of list.getEntries()) tasks.push(entry.duration); }).observe({ type: "longtask" });
    let previous = performance.now();
    const frame = (now: number) => { frames.push(now - previous); previous = now; requestAnimationFrame(frame); };
    requestAnimationFrame(frame);
    Object.assign(window, { renderTasks: tasks, renderFrames: frames });
  });
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const history = Array.from({ length: 40 }, (_, i) => ({ id: i + 1, role: "assistant", ts: i + 1,
    content: `Code example ${i + 1}\n\n\`\`\`python\n${Array.from({ length: 120 }, (_, j) => `result_${j} = process_items(["item", ${j}], enabled=True) # batch ${i}`).join("\n")}\n\`\`\`` }));
  await page.route("**/api/chat/history*", (route) => route.fulfill({ json: history }));
  const cdp = await context.newCDPSession(page);
  await cdp.send("Performance.enable");
  const start = Date.now();
  await page.goto("/webui/");
  await openWebChat(page);
  await expect(page.locator(".message-list [data-message-key]")).toHaveCount(40);
  const readyMs = Date.now() - start;
  await expect(page.locator(".message-list .shiki").last()).toBeAttached({ timeout: 20000 });
  await page.waitForTimeout(5000);
  await expect(page.locator(".message-list [data-message-key]").last().getByRole("button", { name: "Expand", exact: true })).toBeInViewport();
  await cdp.send("HeapProfiler.collectGarbage");
  const heap = await cdp.send("Runtime.getHeapUsage");
  const dom = await cdp.send("Memory.getDOMCounters");
  const metrics = await cdp.send("Performance.getMetrics");
  const timing = await page.evaluate(() => ({
    longTasks: Reflect.get(window, "renderTasks") as number[],
    frames: Reflect.get(window, "renderFrames") as number[],
  }));
  const report = { readyMs, heapMiB: heap.usedSize / 1024 / 1024, ...dom,
    highlighted: await page.locator(".message-list .shiki").count(),
    taskSeconds: metrics.metrics.find((m) => m.name === "TaskDuration")?.value ?? 0,
    longTasks: timing.longTasks.length, longestTaskMs: Math.max(0, ...timing.longTasks),
    longestFrameMs: Math.max(0, ...timing.frames), errors };
  console.log("RENDER_REPORT", JSON.stringify(report));
  await info.attach("render-metrics", { body: JSON.stringify(report, null, 2), contentType: "application/json" });
  await page.screenshot({ path: info.outputPath("code-history.png") });
  expect(errors).toEqual([]);
});
