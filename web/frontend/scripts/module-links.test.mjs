import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { ensureDirectoryLink, syncUiContributions } from "./module-links.mjs";

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

test("module contributions need no page registration and disappear when removed", (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "anelf-contributions-"));
  t.after(() => {
    assert.equal(fs.realpathSync(path.dirname(root)), fs.realpathSync(os.tmpdir()));
    assert.ok(path.basename(root).startsWith("anelf-contributions-"));
    fs.rmSync(root, { recursive: true });
  });
  for (const entry of ["entities/demo/panels/contributions.ts", "channels/demo/frontend/contributions.ts", "entities/_private/panels/contributions.ts"]) {
    const target = path.join(root, entry);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, "export default [];");
  }
  assert.deepEqual(syncUiContributions(root), ["entity:demo", "channel:demo"]);
  const generated = path.join(root, "web/frontend/src/generated/ui-contributions.ts");
  const before = fs.statSync(generated).mtimeMs;
  assert.match(fs.readFileSync(generated, "utf8"), /@entities\/demo\/panels\/contributions/);
  syncUiContributions(root);
  assert.equal(fs.statSync(generated).mtimeMs, before);
  fs.unlinkSync(path.join(root, "entities/demo/panels/contributions.ts"));
  assert.deepEqual(syncUiContributions(root), ["channel:demo"]);
  assert.doesNotMatch(fs.readFileSync(generated, "utf8"), /entity:demo/);
});
