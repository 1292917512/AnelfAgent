import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";

async function exists(target) {
  try { await fs.access(target); return true; }
  catch (error) { if (error.code === "ENOENT") return false; throw error; }
}

async function renameWithRetry(source, destination) {
  for (let attempt = 0; ; attempt++) {
    try { await fs.rename(source, destination); return; }
    catch (error) {
      if (attempt >= 4 || !["EPERM", "EBUSY", "EACCES"].includes(error.code)) throw error;
      await delay(100 * 2 ** attempt);
    }
  }
}

/** Publish a completed frontend build, restoring the previous directory if activation fails. */
export async function swapDist(frontendDir, { rename = renameWithRetry } = {}) {
  const root = path.resolve(frontendDir);
  const dist = path.join(root, "dist");
  const next = path.join(root, "dist.next");
  const backup = path.join(root, `dist.old-${randomUUID()}`);

  for (const directory of [dist, next]) {
    if (!await exists(directory)) continue;
    const stat = await fs.lstat(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error(`Invalid build directory: ${directory}`);
  }
  if (!await exists(path.join(next, "index.html"))) throw new Error("dist.next/index.html is missing");

  const hadPrevious = await exists(dist);
  if (hadPrevious) await rename(dist, backup);
  try {
    await rename(next, dist);
  } catch (error) {
    if (hadPrevious) {
      try { await rename(backup, dist); }
      catch (rollbackError) {
        throw new AggregateError([error, rollbackError], `Build activation and recovery failed; previous build retained at ${backup}`);
      }
    }
    throw error;
  }
  if (hadPrevious) await fs.rm(backup, { recursive: true, force: true });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  swapDist(frontendDir).then(() => console.log("[swap-dist] Build activated")).catch((error) => {
    console.error("[swap-dist]", error);
    process.exitCode = 1;
  });
}
