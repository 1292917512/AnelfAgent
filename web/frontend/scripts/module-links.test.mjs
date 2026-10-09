import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ensureDirectoryLink } from "./module-links.mjs";

test("directory bridge is idempotent and preserves real directories", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "anelf-links-"));
  const target = path.join(root, "target");
  const link = path.join(root, "bridge");
  const own = path.join(root, "own");
  fs.mkdirSync(target);
  fs.mkdirSync(own);
  const marker = path.join(own, "keep.txt");
  fs.writeFileSync(marker, "keep");
  t.after(() => {
    if (fs.lstatSync(link, { throwIfNoEntry: false })) fs.unlinkSync(link);
    fs.unlinkSync(marker);
    fs.rmdirSync(own);
    fs.rmdirSync(target);
    fs.rmdirSync(root);
  });
  ensureDirectoryLink(link, target);
  assert.equal(fs.realpathSync(link), fs.realpathSync(target));
  const before = fs.lstatSync(link).mtimeMs;
  ensureDirectoryLink(link, target);
  assert.equal(fs.lstatSync(link).mtimeMs, before);
  assert.throws(() => ensureDirectoryLink(own, target), /Refusing/);
  assert.equal(fs.readFileSync(marker, "utf8"), "keep");
});
