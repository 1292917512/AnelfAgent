import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./fixtures";

test("Volume backup completion refreshes counts and an open backup list; cancellation waits for cleanup", async ({ page }) => {
  const volume = { volume_id: "memory", name: "Test memory", description: "Authoritative memory",
    kind: "sqlite", capabilities: ["backup", "restore"], path: "/memory.sqlite3", default_path: "/memory.sqlite3",
    location_source: "default", assignment: null, active_path: "/memory.sqlite3", needs_restart: false,
    pending_restore: false, exists: true, size_bytes: 4096, backup_count: 0, last_backup_at: 0 };
  let operation: Record<string, unknown> = { state: "idle", op: null };
  let backups: Record<string, unknown>[] = [];
  await page.route("**/api/database/volumes", route => route.fulfill({ json: { items: [volume], pending_restore: null } }));
  await page.route("**/api/database/volumes/memory/operation", route => route.fulfill({ json: operation }));
  await page.route("**/api/database/volumes/memory/backups", route => route.fulfill({ json: { items: backups } }));
  await page.route("**/api/database/volumes/memory/backup", route => {
    operation = { op: "backup", state: "running", phase: "sqlite_backup", done: 1, total: 4,
      started_at: 100 + volume.backup_count, cancellable: true };
    return route.fulfill({ json: operation });
  });
  await page.route("**/api/database/volumes/memory/operation/cancel", route => {
    operation = { ...operation, cancel_requested: true };
    return route.fulfill({ json: operation });
  });
  await page.goto("/webui/data?tab=volumes");
  await expect(page.getByText("0 backups", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Backup now", exact: true }).click();
  await expect(page.getByText(/Backup in progress/)).toBeVisible();
  await page.getByRole("button", { name: "Backups", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/No backups/)).toBeVisible();
  volume.backup_count = 1;
  volume.last_backup_at = 101;
  backups = [{ backup_id: "20261022-101", created_at: 101, kind: "sqlite", artifact: "volume.sqlite3",
    size_bytes: 4096, table_count: 1, file_count: 0, consistency: "online_backup", sha256: "abc" }];
  operation = { ...operation, state: "done", finished_at: 102, cancellable: false };
  await expect(dialog.getByText("20261022-101", { exact: true })).toBeVisible();
  await expect(page.getByText(/1 backups/)).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "Backup now", exact: true }).click();
  await page.getByRole("button", { name: "Cancel operation", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancelling safely…", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Backup now", exact: true })).toBeDisabled();
  operation = { ...operation, state: "cancelled", finished_at: 103, cancellable: false };
  await expect(page.getByText("Operation cancelled. Temporary output removed; source data unchanged.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Backup now", exact: true })).toBeEnabled();
  await expect(page.getByText(/1 backups/)).toBeVisible();
});

test("A backup finishing before the first poll still refreshes its count", async ({ page }) => {
  let count = 0;
  await page.route("**/api/database/volumes", route => route.fulfill({ json: { items: [{
    volume_id: "notes", name: "Notes", description: "", kind: "notes_tree", capabilities: ["backup"],
    path: "/notes", location_source: "default", exists: true, size_bytes: 2, backup_count: count, last_backup_at: 0,
  }], pending_restore: null } }));
  const status = () => count ? { op: "backup", state: "done", started_at: 1, finished_at: 2 } : { op: null, state: "idle" };
  await page.route("**/api/database/volumes/notes/operation", route => route.fulfill({ json: status() }));
  await page.route("**/api/database/volumes/notes/backup", route => {
    count = 1;
    return route.fulfill({ json: status() });
  });
  await page.goto("/webui/data?tab=volumes");
  await page.getByRole("button", { name: "Backup now", exact: true }).click();
  await expect(page.getByText("1 backups", { exact: true })).toBeVisible();
});

test("Cognee isolates a native fault and offers retry before the queue is exhausted", async ({ page }) => {
  const state = {
    availability: { installed: true, enabled: true, ready: false, reason: "Native worker stopped" },
    sync: { enabled: true, running: true, pending: 1, synced: 20, failed: 0,
      publication: { phase: "failed", error: "Native worker stopped", commits: 4, cloned_bytes: 1024, copied_bytes: 0 } },
    storage: { total_bytes: 2048, graph_bytes: 512, lance_bytes: 512, data_bytes: 0, metadata_bytes: 0,
      other_bytes: 0, protection_bytes: 1024 },
  };
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: state }));
  let retries = 0;
  await page.route("**/api/memory/cognee/retry", (route) => {
    retries++;
    return route.fulfill({ json: { retried: 0 } });
  });
  await page.goto("/webui/memory?tab=cognee");
  await expect(page.getByText("Index protection: Fault isolated; retry required")).toBeVisible();
  await expect(page.getByText("4 batches safely committed")).toBeVisible();
  await expect(page.getByText("Index protection snapshots", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Retry Failed", exact: true }).click();
  expect(retries).toBe(1);
});

test("Cognee explicitly starts a fresh generation during an unfinished build", async ({ page }) => {
  const state = {
    availability: { installed: true, enabled: true, ready: true },
    sync: { enabled: true, running: true, pending: 10, synced: 2, failed: 0 },
    projection: { phase: "building", namespace: "old", rebuild_requested: false, verified: 0,
      counts: { memories: 12, graph_nodes: 0 }, previous_namespaces: [] },
  };
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: state }));
  const requests: boolean[] = [];
  await page.route("**/api/memory/cognee/rebuild?*", (route) => {
    requests.push(new URL(route.request().url()).searchParams.get("restart") === "true");
    state.projection.rebuild_requested = true;
    return route.fulfill({ json: { ok: true, scheduled: true } });
  });
  await page.goto("/webui/memory?tab=cognee");
  await expect(page.getByRole("button", { name: "Build / Resume", exact: true })).toBeDisabled();
  const fresh = page.getByRole("button", { name: "Start a fresh full rebuild", exact: true });
  await fresh.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText(/Source content, previous datasets and backups are retained/)).toBeVisible();
  expect(requests).toEqual([]);
  await dialog.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(fresh).toBeDisabled();
  expect(requests).toEqual([true]);
});

test("Cognee generation cleanup preserves a fallback until acceptance and confirms the exact round", async ({ page }, info) => {
  test.setTimeout(60000);
  const state = {
    availability: { installed: true, enabled: true, ready: true, version: "1.6.5" },
    sync: { enabled: true, running: true, pending: 10, synced: 2, failed: 0 },
    projection: { phase: "building", namespace: "anelf_p2_new", verified: 0, counts: { memories: 12, graph_nodes: 0 },
      previous_namespaces: ["anelf", "anelf_p2_old"], rebuild_requested: false, memory_scanned: true, graph_scanned: true, backup_path: "/source-backup.sqlite3" },
    storage: { total_bytes: 4096, graph_bytes: 1024, lance_bytes: 2048, data_bytes: 512, metadata_bytes: 256, other_bytes: 256,
      computed_at: Date.now() / 1000 },
    retirement: { running: false, stage: "", dataset_name: "", documents: 0, preserved: 0, backup_path: "", error: "" },
  };
  let generations = [
    { namespace: "anelf_p2_new", current: true, phase: "building", document_count: 2, processed_count: 2, storage: null,
      datasets: [{ id: "current", name: "anelf_p2_new_global", document_count: 2 }] },
    { namespace: "anelf_p2_old", current: false, phase: "unknown", document_count: 26, processed_count: 20,
      storage: { total_bytes: 1536, graph_bytes: 512, lance_bytes: 1024 },
      datasets: [{ id: "old", name: "anelf_p2_old_global", document_count: 10 }, { id: "old-rel", name: "anelf_p2_old_relations", document_count: 16 }] },
    { namespace: "anelf", current: false, phase: "unknown", document_count: 2, processed_count: 2, storage: null,
      datasets: [{ id: "base", name: "anelf_global", document_count: 2 }] },
  ];
  await page.route("**/api/memory/cognee/status", route => route.fulfill({ json: state }));
  await page.route("**/api/memory/cognee/datasets", route => route.fulfill({ json: { ready: true, busy: false, updated_at: 1, error: "",
    generations, items: generations.flatMap(g => g.datasets) } }));
  const deleted: string[] = [];
  await page.route("**/api/memory/cognee/generations/*", route => {
    expect(route.request().method()).toBe("DELETE");
    deleted.push(new URL(route.request().url()).pathname.split("/").pop() ?? "");
    Object.assign(state.retirement, { running: true, stage: "preserving", dataset_name: "anelf_p2_old", documents: 26, preserved: 12 });
    return route.fulfill({ json: state.retirement });
  });
  await page.goto("/webui/memory?tab=cognee");
  const old = page.getByRole("article", { name: "anelf_p2_old", exact: true });
  const cleanup = old.getByRole("button", { name: "Clean up this generation", exact: true });
  await expect(old.getByText("26 confirmed documents · 20 marked extracted · 2 datasets")).toBeVisible();
  await expect(old.getByText("Dedicated indexes 1.5 KB · Graph 512 B · Vectors 1.0 KB")).toBeVisible();
  await expect(cleanup).toBeDisabled();
  Object.assign(state.sync, { enabled: false, running: false });
  await page.reload();
  await expect(cleanup).toBeEnabled();
  await old.getByRole("button", { name: "View generation details" }).click();
  const detail = page.getByRole("dialog", { name: "View generation details", exact: true });
  await expect(detail.getByText("anelf_p2_old_relations", { exact: true })).toBeVisible();
  await expect(detail.getByText(/Historical acceptance status was not recorded/)).toBeVisible();
  await detail.getByRole("button", { name: "Clean up this generation", exact: true }).click();
  const confirm = page.getByRole("dialog", { name: "Clean up this generation", exact: true });
  await expect(confirm.getByText(/Clean up generation “anelf_p2_old”/)).toBeVisible();
  await expect(confirm.getByText(/2 datasets and 26 confirmed documents/)).toBeVisible();
  expect(deleted).toEqual([]);
  await confirm.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(detail).toBeVisible();
  expect(deleted).toEqual([]);
  await detail.getByRole("button", { name: "Clean up this generation", exact: true }).click();
  await confirm.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(detail.getByRole("button", { name: "Clean up this generation", exact: true })).toBeDisabled();
  await detail.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.getByText(/Preserved 12 \/ 26 documents/)).toBeVisible();
  expect(deleted).toEqual(["anelf_p2_old"]);
  generations = generations.filter(g => g.namespace !== "anelf_p2_old");
  state.projection.previous_namespaces = ["anelf"];
  Object.assign(state.retirement, { running: false, stage: "completed" });
  await page.reload();
  const last = page.getByRole("article", { name: "anelf", exact: true });
  await expect(last.getByRole("button", { name: "Clean up this generation" })).toBeDisabled();
  await expect(last.getByText(/Keep at least one old generation/)).toBeVisible();
  Object.assign(state.projection, { phase: "ready", verified: 12 });
  state.sync.pending = 0;
  await page.reload();
  await expect(last.getByRole("button", { name: "Clean up this generation" })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("cognee-generations.png"), fullPage: true });
});

test("Cognee exposes in-flight progress, retries and stale observations", async ({ page }, info) => {
  test.setTimeout(60000);
  const now = Date.now() / 1000;
  let unavailable = false;
  const state = {
    availability: { installed: true, enabled: true, ready: true, version: "1.6.5" },
    sync: { enabled: true, running: true, pending: 100, processing: 10, synced: 0, failed: 0,
      retrying: 10, next_retry_at: now + 10, last_error: "Model request timed out", configuration_pending: true,
      active: { stage: "cognify", items: 10, attempt: 2, dataset_name: "test_global", started_at: now - 600,
        elapsed_seconds: 600, stage_elapsed_seconds: 590, quiet_seconds: 310, last_change_at: now - 310,
        timeout_seconds: 3600, progress_checked_at: now, progress_error: "",
        pipeline: { status: "DATASET_PROCESSING_STARTED", completed_items: 9, total_items: 10, current_stage: "add_data_points" } } },
    projection: { phase: "scanning", rebuild_requested: false, namespace: "test", verified: 0,
      counts: { memories: 1825, graph_nodes: 2506, excluded_memories: 118 }, previous_namespaces: ["old"],
      backup_path: "/data/backups/memory.sqlite3", completed_at: 0, legacy_preserved: 26 },
  };
  await page.route("**/api/memory/cognee/status", (route) => unavailable
    ? route.fulfill({ status: 503, json: { detail: "Status temporarily unavailable" } })
    : route.fulfill({ json: state }));
  await page.goto("/webui/memory?tab=cognee");
  const batch = page.getByTestId("cognee-batch-progress");
  await expect(page.getByText("Committed 0 / initially expected 4331 documents")).toBeVisible();
  await expect(page.getByText("Expected rebuild total: 4331 documents")).toBeVisible();
  await expect(page.getByText("1825 memories + 2506 relationship nodes (inventory at build start)")).toBeVisible();
  await expect(batch.getByText("Cognee pipeline processed 9 / 10 documents")).toBeVisible();
  await expect(batch.getByText(/No progress change observed for over 5 minutes/)).toBeVisible();
  await expect(page.getByText(/10 previously failed items remain/)).toBeVisible();
  await expect(page.getByText(/New model\/settings saved; waiting for the current batch/)).toBeVisible();
  await page.getByRole("button", { name: "Build details", exact: true }).click();
  await expect(page.getByText(/A normal restart resumes the saved queue/)).toBeVisible();
  await page.getByRole("dialog", { name: "Build details", exact: true }).getByRole("button", { name: "Close", exact: true }).click();
  const saves: unknown[] = [];
  Object.assign(state.sync, { pause_requested: false });
  await page.route("**/api/memory/cognee/config", (route) => {
    if (route.request().method() === "PUT") {
      const update = route.request().postDataJSON();
      saves.push(update);
      Object.assign(state.sync, { pause_requested: !update.sync_enabled, configuration_pending: !update.sync_enabled });
      if (update.sync_enabled) state.sync.enabled = state.sync.running = true;
      return route.fulfill({ json: { sync_enabled: update.sync_enabled } });
    }
    return route.fulfill({ json: { enabled: true, sync_enabled: state.sync.enabled, chat: {}, embedding: {} } });
  });
  await page.getByRole("button", { name: "Pause sync", exact: true }).click();
  await expect(page.getByRole("button", { name: "Finishing current batch…", exact: true })).toBeDisabled();
  await expect(page.getByText(/Pause requested: finish this batch/)).toBeVisible();
  expect(saves).toEqual([{ sync_enabled: false }]);
  Object.assign(state.sync, { enabled: false, running: false, pause_requested: false, configuration_pending: false });
  await page.reload();
  await expect(page.getByText(/Sync paused; the queue is preserved/)).toBeVisible();
  await page.getByRole("button", { name: "Resume sync", exact: true }).click();
  await expect(page.getByRole("button", { name: "Pause sync", exact: true })).toBeEnabled();
  expect(saves).toEqual([{ sync_enabled: false }, { sync_enabled: true }]);
  await batch.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await batch.screenshot({ path: info.outputPath("cognee-progress.png") });
  state.sync.active.progress_error = "Progress endpoint timeout";
  await expect(batch.getByText(/Progress read failed/)).toBeVisible({ timeout: 12000 });
  await expect(batch.getByText("Cognee pipeline processed 9 / 10 documents")).toBeVisible();
  unavailable = true;
  await expect(page.getByText(/Status refresh failed. This is the last snapshot/)).toBeVisible({ timeout: 25000 });
  await expect(batch.getByText("Cognee pipeline processed 9 / 10 documents")).toBeVisible();
});

test("Cognee catalog distinguishes initial loading and keeps confirmed datasets visible during writes", async ({ page }, info) => {
  let ready = false;
  let datasetReads = 0;
  let phase = "ready";
  let catalogReady = false;
  let busy = false;
  const enhancements: unknown[] = [];
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready, configuration_required: !ready, version: "1.6.2", reason: ready ? "" : "Configure chat and embedding models" },
    resolved: {}, sync: { running: true, pending: 2, failed: 0, synced: 0, last_error: "" },
    projection: { phase, namespace: "anelf_p2_test", verified: 1, legacy_preserved: 26, counts: {}, previous_namespaces: ["anelf_old"] },
  } }));
  await page.route("**/api/memory/probe-status", (route) => route.fulfill({ json: { enabled: true, inject_enabled: true, counters: {}, scopes: [] } }));
  await page.route("**/api/memory/cognee/config", (route) => route.fulfill({ json: {
    enabled: true, sync_enabled: true, recall_enabled: true,
    chat: { source: "auto", model_id: "", extra_args: {} },
    embedding: { source: "auto", model_id: "" },
  } }));
  await page.route("**/api/memory/cognee/datasets", (route) => {
    datasetReads++;
    return route.fulfill({ json: { ready: catalogReady, busy, updated_at: catalogReady ? 1 : 0, error: "",
      items: catalogReady ? [{ id: "dataset-1", name: "anelf_p2_test_global" }, { id: "dataset-old", name: "anelf_old_global" }] : [],
      generations: catalogReady ? [
        { namespace: "anelf_p2_test", current: true, phase, document_count: 1, processed_count: 1, storage: null,
          datasets: [{ id: "dataset-1", name: "anelf_p2_test_global" }] },
        { namespace: "anelf_old", current: false, phase: "unknown", document_count: null, processed_count: null, storage: null,
          datasets: [{ id: "dataset-old", name: "anelf_old_global" }] },
      ] : [],
    } });
  });
  await page.route("**/api/memory/cognee/improve", (route) => {
    enhancements.push(route.request().postDataJSON());
    return route.fulfill({ json: { ok: true } });
  });
  await page.goto("/webui/memory?tab=cognee");
  await expect(page.getByText("Awaiting configuration", { exact: true })).toHaveCount(2);
  await expect(page.getByRole("button", { name: "Compact Storage", exact: true })).toBeDisabled();
  await expect(page.getByText(/Waiting for the first catalog observation/)).toBeVisible();
  expect(datasetReads).toBe(1);
  catalogReady = true;
  ready = true;
  await page.reload();
  await page.getByRole("article", { name: "anelf_p2_test", exact: true }).getByRole("button", { name: "View generation details" }).click();
  await expect(page.getByText("anelf_p2_test_global", { exact: true })).toBeVisible();
  await expect(page.getByText(/Preserved 26 complete legacy projection documents/)).toBeVisible();
  const summary = page.getByRole("button", { name: "Build global summaries", exact: true });
  const conflicts = page.getByRole("button", { name: "Review fact conflicts", exact: true });
  await expect(summary.nth(0)).toBeEnabled();
  await expect(summary).toHaveCount(1);
  await expect(conflicts).toHaveCount(1);
  expect(datasetReads).toBe(2);
  await summary.nth(0).click();
  await expect(conflicts.nth(0)).toBeEnabled();
  await conflicts.nth(0).click();
  await expect.poll(() => enhancements).toEqual([
    { dataset_name: "anelf_p2_test_global", build_global_context_index: true, review_conflicts: false },
    { dataset_name: "anelf_p2_test_global", build_global_context_index: false, review_conflicts: true },
  ]);
  await page.screenshot({ path: info.outputPath("cognee-ready.png"), fullPage: true });
  busy = true;
  await page.reload();
  await expect(page.getByText(/The index is busy/)).toBeVisible();
  await expect(page.getByText(/Catalog confirmed at/)).toBeVisible();
  await page.getByRole("article", { name: "anelf_p2_test", exact: true }).getByRole("button", { name: "View generation details" }).click();
  await expect(page.getByText("anelf_p2_test_global", { exact: true })).toBeVisible();
  await expect(summary.nth(0)).toBeDisabled();
  await expect(conflicts.nth(0)).toBeDisabled();
  phase = "building";
  busy = false;
  await page.reload();
  await page.getByRole("article", { name: "anelf_p2_test", exact: true }).getByRole("button", { name: "View generation details" }).click();
  await expect(summary.nth(0)).toBeDisabled();
});
import type { CapabilityStatus, ConfigMetaItem } from "../src/lib/types";

test("Cognee reasoning edits survive failures and independent saves", async ({ page }, info) => {
  let rejectSave = true;
  let pending = false;
  let config = {
    enabled: true, sync_enabled: true, recall_enabled: true,
    chat: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", api_version: "", instructor_mode: "", max_completion_tokens: 0, reasoning_effort: "", extra_args: {} },
    embedding: { source: "auto", model_id: "", provider: "openai", model: "", api_key: "", endpoint: "", dimensions: 0 },
    options: { chat_providers: ["openai"], embed_providers: ["openai"], instructor_modes: [""], reasoning_efforts: ["", "off", "low", "high"] },
  };
  await page.route("**/api/memory/cognee/config", async (route) => {
    if (route.request().method() === "PUT") {
      if (rejectSave) return route.fulfill({ status: 400, json: { detail: "Thinking budget must be lower than the output limit" } });
      config = { ...config, ...route.request().postDataJSON() };
      pending = true;
    }
    return route.fulfill({ json: config });
  });
  await page.route("**/api/memory/cognee/status", (route) => route.fulfill({ json: {
    availability: { installed: true, enabled: true, ready: true, configuration_required: false, version: "1.6.4", reason: "" },
    resolved: { chat: { provider: "openai", model: "openai/glm-test" } },
    sync: { running: true, pending: 1, failed: 0, synced: 3, last_error: "", configuration_pending: pending },
  } }));
  await page.route("**/api/memory/probe-status", (route) => route.fulfill({ json: { enabled: true, inject_enabled: true, counters: {}, scopes: [] } }));
  await page.route("**/api/memory/cognee/datasets", (route) => route.fulfill({ status: 503, json: { detail: "Dataset store temporarily unavailable" } }));
  await page.goto("/webui/memory?tab=cognee");
  await page.getByRole("button", { name: "Models and configuration", exact: true }).click();
  const chat = page.locator("section").filter({ has: page.getByRole("heading", { name: "Extraction Model", exact: true }) });
  const embedding = page.locator("section").filter({ has: page.getByRole("heading", { name: "Embedding Model", exact: true }) });
  const effort = chat.getByRole("combobox", { name: "Reasoning effort", exact: true });
  await effort.selectOption("off");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect(chat.getByRole("alert")).toHaveText("Thinking budget must be lower than the output limit");
  await expect(effort).toHaveValue("off");
  rejectSave = false;
  await embedding.getByRole("button", { name: "Custom", exact: true }).click();
  await embedding.getByLabel("Model identifier").fill("openai/embedding-test");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect(chat.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  await expect(embedding.getByLabel("Model identifier")).toHaveValue("openai/embedding-test");
  await expect(page.getByText(/Configuration saved.*/).first()).toBeVisible();
  await effort.selectOption("high");
  await embedding.getByRole("button", { name: "Save", exact: true }).click();
  await expect(embedding.getByRole("button", { name: "Saved", exact: true })).toBeVisible();
  await expect(effort).toHaveValue("high");
  await chat.getByRole("button", { name: "Save", exact: true }).click();
  await expect.poll(() => config.chat.reasoning_effort).toBe("high");
  await expect(page.getByText("Dataset store temporarily unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("openai/glm-test", { exact: true })).toBeVisible();
  await chat.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await chat.screenshot({ path: info.outputPath("cognee-model-card.png") });
  await page.screenshot({ path: info.outputPath("cognee-config.png"), fullPage: true });
});

test("capability editing preserves automatic chains and failed edits", async ({ page }, info) => {
  let overrides: Record<string, string[]> = {};
  const defaults = { tts: ["models", "minimax"], voice_mgmt: ["minimax"], music: ["models"] };
  await page.route("**/api/audio/capabilities", (route) => route.fulfill({ json: {
    providers: [
      { name: "models", capabilities: ["tts", "music"], configured: { tts: true, music: true }, details: {} },
      { name: "minimax", capabilities: ["tts", "voice_mgmt"], configured: { tts: true, voice_mgmt: true }, details: {} },
    ], defaults, overrides, chains: { ...defaults, ...overrides },
  } satisfies CapabilityStatus }));
  const writes: Record<string, string[]>[] = [];
  let fail = true;
  await page.route("**/api/config/meta/sound_provider_priority", async (route) => {
    const body: { value: Record<string, string[]> } = route.request().postDataJSON();
    writes.push(body.value);
    if (fail) { await route.fulfill({ status: 503, json: { detail: "Configuration storage unavailable" } }); return; }
    overrides = body.value;
    await route.fulfill({ json: { status: "ok" } });
  });
  await page.goto("/webui/sound?tab=generation");
  await page.getByRole("button", { name: "Move MiniMax up" }).and(page.locator(":enabled")).click();
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Configuration storage unavailable" }).first()).toBeVisible();
  expect(writes[0]).toEqual({ tts: ["minimax", "models"] });
  fail = false;
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByText("Priority changes are not saved")).toHaveCount(0);
  await page.getByRole("button", { name: "Restore automatic" }).click();
  await page.getByRole("button", { name: "Save priority", exact: true }).click();
  await expect(page.getByText("Priority changes are not saved")).toHaveCount(0);
  expect(writes[writes.length - 1]).toEqual({});
  await page.screenshot({ path: info.outputPath("capability-chains.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("vision preview targets sources and metadata preserves non-numeric config", async ({ page }, info) => {
  const now = Date.now() / 1000;
  const frame = { path: "workspace/uploads/camera.png", source: "external:desk", captured_at: now, width: 640, height: 360 };
  await page.route("**/api/vision/status", (route) => route.fulfill({ json: {
    watching: [], sources: {}, latest: frame, frames: { [frame.source]: frame }, last_change_at: now,
    injection: { active: true, provider: "vision", last_text_inject_at: now, last_media_inject: null },
  } }));
  await page.route("**/api/vision/sources", (route) => route.fulfill({ json: { sources: [
    { key: frame.source, display_name: "Desk camera", description: "Workspace camera", pollable: false, can_capture: false, enabled: true, watching: false },
  ] } }));
  const imageUrls: URL[] = [];
  await page.route("**/api/vision/latest?*", (route) => {
    imageUrls.push(new URL(route.request().url()));
    return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#24364b"/><rect x="80" y="60" width="480" height="240" rx="16" fill="#344b66"/><text x="320" y="188" text-anchor="middle" fill="#dce7f5" font-family="sans-serif" font-size="24">Desk camera · preview</text></svg>' });
  });
  const defaults: ConfigMetaItem = { key: "vision_disabled_sources", type: "string", description: "Disabled sources", value: "external:old", default: "", editable: true, options: [], advanced: false, min: null, max: null, step: null, unit: "", tag: "", source: "config_manager", value_source: "configured", environment_variable: null };
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "vision", items: [defaults] }] } }));
  const writes: unknown[] = [];
  await page.route("**/api/config/meta/vision_disabled_sources", (route) => {
    const body: { value: unknown } = route.request().postDataJSON(); writes.push(body.value);
    return route.fulfill({ json: { status: "ok", value: body.value } });
  });
  await page.goto("/webui/vision");
  await expect(page.getByAltText("Latest frame")).toBeVisible();
  await expect.poll(() => imageUrls.length).toBeGreaterThan(0);
  expect(imageUrls[0]?.searchParams.get("source")).toBe("external:desk");
  expect(imageUrls[0]?.searchParams.has("v")).toBe(true);
  await page.screenshot({ path: info.outputPath("vision-overview.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa"]).analyze();
  expect(accessibility.violations.map((item) => item.id)).toEqual([]);
  await page.getByRole("tab", { name: "Config", exact: true }).click();
  const input = page.getByRole("textbox", { name: "Disabled sources", exact: true });
  await expect(input).toHaveValue("external:old");
  await input.fill("external:desk");
  await input.press("Enter");
  await expect.poll(() => writes.length).toBe(1);
  expect(writes[0]).toBe("external:desk");
});

test("audio overview loads recordings on demand and shows probe errors", async ({ page }, info) => {
  await page.route("**/api/audio/status", (route) => route.fulfill({ json: {
    providers: [
      { name: "Transcription service", kind: "asr", available: true, priority: 1, state: "ready" },
      { name: "Identity service", kind: "voiceprint", available: false, priority: 2, state: "timeout", hint: "Status probe timed out; retry to check availability." },
    ], asr_available: true, voiceprint_available: false, tts_providers: [{ name: "Speech synthesis", available: true, priority: 1, state: "ready" }], tts_available: true,
    realtime: { enabled: true, mode: "cascade", sessions: 0, owners: [], states: {} }, voice_config: {},
    library: { segments: 152, speakers: 6, pending_speakers: 2, bound_speakers: 4, unread_segments: 12, audio_ms: 720000, db_path: "config/memory/audio.sqlite3" },
    injection: { provider: "audio_status", active: true },
  } }));
  let requests = 0;
  await page.route("**/api/audio/recordings?*", (route) => {
    requests++;
    const offset = Number(new URL(route.request().url()).searchParams.get("offset"));
    return route.fulfill({ json: { total: 13, items: Array.from({ length: offset ? 1 : 12 }, (_, i) => ({
      path: "workspace/uploads/recordings/" + (offset + i) + ".wav", status: "done", file_count: 1, segments: 3, error: "",
    })) } });
  });
  await page.goto("/webui/sound");
  await expect(page.getByText("Probe timed out", { exact: true })).toBeVisible();
  expect(requests).toBe(0);
  await page.screenshot({ path: info.outputPath("audio-overview.png"), fullPage: true });
  await page.getByRole("button", { name: "Browse recordings", exact: true }).click();
  await expect(page.getByText("workspace/uploads/recordings/0.wav", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Load more", exact: true }).click();
  await expect(page.getByText("workspace/uploads/recordings/12.wav", { exact: true })).toBeVisible();
  expect(requests).toBe(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("tag purposes link to tools and retrieval failures stay visible", async ({ page }, info) => {
  await page.route("**/api/tags/unified", (route) => route.fulfill({ json: [
    { name: "speaker_scope", description: "Speaker identity and conversation origin", builtin: true, sources: ["message"], message_key: true, tool_count: 0, tools: [] },
    { name: "media:image", description: "Image understanding and generation", builtin: true, sources: ["tool"], message_key: false, tool_count: 2, tools: ["recognize_image", "generate_image"] },
    { name: "custom_ops", description: "Personal maintenance tools", builtin: false, sources: ["custom", "tool", "message"], message_key: true, tool_count: 1, tools: ["check_service"] },
  ] }));
  await page.goto("/webui/tags");
  const cards = page.locator("main article");
  await expect(cards).toHaveCount(3);
  const cardHeights = await cards.evaluateAll((entries) => entries.map((entry) => entry.getBoundingClientRect().height));
  expect(Math.max(...cardHeights) - Math.min(...cardHeights)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: info.outputPath("tag-catalog.png"), fullPage: true });
  await page.getByRole("button", { name: "View tag details: custom_ops", exact: true }).click();
  const customDetail = page.getByRole("dialog");
  await expect(customDetail.getByRole("button", { name: "Delete tag custom_ops" })).toBeDisabled();
  await customDetail.getByText("Close", { exact: true }).click();
  await expect(customDetail).toBeHidden();
  await page.getByRole("textbox", { name: "Search tags, descriptions or linked tools…" }).fill("recognize_image");
  await page.getByRole("button", { name: "View tag details: media:image", exact: true }).click();
  const toolDetail = page.getByRole("dialog");
  await expect(toolDetail.getByRole("link", { name: "View in Tools", exact: true })).toHaveAttribute("href", "/webui/tools?tag=media%3Aimage");
  await expect(toolDetail.getByText("recognize_image", { exact: true })).toBeVisible();
  await page.screenshot({ path: info.outputPath("tag-detail.png"), fullPage: true });
  await page.route("**/api/retrieval/matrix", (route) => route.fulfill({ json: {
    capabilities: ["search", "reader", "repo"], selection: { search: "auto", reader: "auto", repo: "auto" }, active: { search: "demo", reader: "builtin" },
    providers: [
      { name: "demo", display_name: "Research service", description: "Search across published sources", enabled: true, configured: true, requires_credential: true, credential_source: "config", capabilities: ["search"] },
      { name: "builtin", display_name: "Web reader", description: "Read and extract web documents", enabled: true, configured: true, requires_credential: false, credential_source: "", capabilities: ["reader"] },
    ],
  } }));
  await page.route("**/api/retrieval/providers/demo/test", (route) => route.fulfill({ json: { ok: false, latency_ms: 12, error: "Search quota exceeded" } }));
  await page.goto("/webui/retrieval");
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Research service", exact: true }) });
  await card.locator("summary").click();
  await card.getByRole("button", { name: "Test", exact: true }).click();
  await expect(card.getByText("Search quota exceeded")).toBeVisible();
  await page.screenshot({ path: info.outputPath("retrieval-providers.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("self-hosted retrieval config uses metadata and refreshes availability", async ({ page }, info) => {
  let endpoint = "";
  const field: ConfigMetaItem = { key: "retrieval_crawl4ai_base_url", type: "string", description: "Local crawler URL", value: "", default: "", editable: true, options: [], advanced: false, min: null, max: null, step: null, unit: "", tag: "", source: "config_manager", value_source: "configured", environment_variable: null };
  await page.route("**/api/retrieval/matrix", (route) => route.fulfill({ json: {
    capabilities: ["search", "reader", "repo", "crawl"], selection: {}, active: { reader: "builtin", crawl: endpoint ? "crawl4ai" : "" },
    providers: [{ name: "crawl4ai", display_name: "Crawl4AI", description: "Local browser rendering", enabled: true, configured: !!endpoint, requires_credential: true, credential_source: "config", config_group: "retrieval/crawl4ai", capabilities: ["reader", "crawl"] }],
  } }));
  await page.route("**/api/config/meta", (route) => route.fulfill({ json: { groups: [{ group: "retrieval/crawl4ai", items: [{ ...field, value: endpoint }] }] } }));
  await page.route("**/api/config/meta/retrieval_crawl4ai_base_url", (route) => {
    endpoint = route.request().postDataJSON().value;
    return route.fulfill({ json: { status: "ok", value: endpoint } });
  });
  await page.route("**/api/retrieval/providers/crawl4ai/test", (route) => route.fulfill({ json: { ok: true, latency_ms: 25, summary: "Example", excerpt: "Rendered page content" } }));
  await page.goto("/webui/retrieval");
  const card = page.locator("section").filter({ has: page.getByRole("heading", { name: "Crawl4AI", exact: true }) });
  await card.locator("summary").click();
  await expect(card.getByRole("button", { name: "Test", exact: true })).toBeDisabled();
  await card.getByRole("button", { name: "Service configuration" }).click();
  const dialog = page.getByRole("dialog", { name: "Crawl4AI" });
  const input = dialog.getByRole("textbox", { name: "Local crawler URL", exact: true });
  await input.fill("http://127.0.0.1:11235");
  await input.press("Enter");
  await expect.poll(() => endpoint).toBe("http://127.0.0.1:11235");
  await page.screenshot({ path: info.outputPath("retrieval-service-config.png"), fullPage: true });
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(card.getByRole("button", { name: "Test", exact: true })).toBeEnabled();
  await card.getByRole("combobox", { name: "Capability to test" }).selectOption("crawl");
  await card.getByRole("button", { name: "Test", exact: true }).click();
  await expect(card.getByText("Rendered page content")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("local retrieval updates remain observable and missing Docker is actionable", async ({ page }, info) => {
  let available = false;
  let posts = 0;
  let snapshots = 0;
  let operation: null | { id: string; action: string; status: string; phase: string; started_at: number; finished_at: number | null; error: string; log: string } = null;
  await page.route("**/api/retrieval/matrix", (route) => route.fulfill({ json: { capabilities: [], providers: [], selection: {}, active: {} } }));
  await page.route("**/api/retrieval/deployment**", (route) => {
    if (route.request().method() === "POST") {
      posts++;
      expect(route.request().postDataJSON()).toEqual({ action: "update", images: { crawl4ai: "unclecode/crawl4ai:0.9.5" } });
      operation = { id: "op-1", action: "update", status: "running", phase: "pulling", started_at: Date.now() / 1000, finished_at: null, error: "", log: "" };
    } else if (operation?.status === "running" && ++snapshots > 1) {
      operation = { ...operation, status: "failed", phase: "recovering", finished_at: Date.now() / 1000, error: "Health check failed; previous version restored." };
    }
    return route.fulfill({ json: {
      available, initialized: available, rollback_available: false,
      operation: operation && { ...operation, elapsed_seconds: 30, phase_elapsed_seconds: 20, output_idle_seconds: 1,
        log: "crawl4ai Downloading layer 12 MB / 40 MB" },
      images: { crawl4ai: "unclecode/crawl4ai:0.9.4", searxng: "searxng/searxng:2026.10.13-22c64b8d3" },
      ports: { crawl4ai: "11235", searxng: "18080" }, containers: [],
      requirements: { platform: "posix", docker_cli: available, install_guide: "https://docs.docker.com/engine/install/" },
    } });
  });
  await page.goto("/webui/retrieval");
  await page.getByRole("button", { name: "Local services", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Local services" });
  await expect(dialog.getByRole("link", { name: "Official Docker installation guide" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Start / initialize" })).toBeDisabled();
  available = true;
  await dialog.getByRole("button", { name: "Refresh status" }).click();
  await expect(dialog.getByRole("button", { name: "Start / initialize" })).toBeEnabled();
  await dialog.locator("summary").filter({ hasText: "Update service versions" }).click();
  await dialog.getByRole("textbox", { name: "Crawl4AI" }).fill("unclecode/crawl4ai:0.9.5");
  await dialog.getByRole("button", { name: "Download and update" }).click();
  await expect(dialog.getByText("Downloading images", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Total 30s", { exact: true })).toBeVisible();
  await expect(dialog.getByText("Current phase 20s", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Stop", exact: true })).toBeDisabled();
  await expect(dialog.getByText("Health check failed; previous version restored.")).toBeVisible();
  expect(posts).toBe(1);
  await page.screenshot({ path: info.outputPath("retrieval-deployment.png"), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
