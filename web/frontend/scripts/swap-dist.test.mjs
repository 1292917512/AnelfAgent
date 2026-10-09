import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { swapDist } from "./swap-dist.mjs";

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "anelf-build-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "dist"));
  await fs.writeFile(path.join(root, "dist/index.html"), "old");
  await fs.mkdir(path.join(root, "dist.next"));
  await fs.writeFile(path.join(root, "dist.next/index.html"), "new");
  return root;
}

test("publishes complete builds and removes only its own backup", async (t) => {
  const root = await fixture(t);
  await fs.mkdir(path.join(root, "dist.old-recovery"));
  await swapDist(root);
  assert.equal(await fs.readFile(path.join(root, "dist/index.html"), "utf8"), "new");
  assert.deepEqual((await fs.readdir(root)).sort(), ["dist", "dist.old-recovery"]);
});

test("keeps the active build when the new build is incomplete", async (t) => {
  const root = await fixture(t);
  await fs.unlink(path.join(root, "dist.next/index.html"));
  await assert.rejects(swapDist(root), /index.html is missing/);
  assert.equal(await fs.readFile(path.join(root, "dist/index.html"), "utf8"), "old");
});

test("restores the previous build after activation fails", async (t) => {
  const root = await fixture(t);
  await assert.rejects(swapDist(root, { rename: async (source, destination) => {
    if (source === path.join(root, "dist.next")) throw new Error("activation blocked");
    await fs.rename(source, destination);
  } }), /activation blocked/);
  assert.equal(await fs.readFile(path.join(root, "dist/index.html"), "utf8"), "old");
  assert.equal(await fs.readFile(path.join(root, "dist.next/index.html"), "utf8"), "new");
});

test("retains the recovery directory when rollback also fails", async (t) => {
  const root = await fixture(t);
  await assert.rejects(swapDist(root, { rename: async (source, destination) => {
    if (source !== path.join(root, "dist")) throw new Error("directory locked");
    await fs.rename(source, destination);
  } }), AggregateError);
  const backup = (await fs.readdir(root)).find((name) => name.startsWith("dist.old-"));
  assert.ok(backup);
  assert.equal(await fs.readFile(path.join(root, backup, "index.html"), "utf8"), "old");
});
