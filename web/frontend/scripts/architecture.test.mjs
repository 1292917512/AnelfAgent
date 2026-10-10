import test from "node:test";
import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import ts from "typescript";

const root = fileURLToPath(new URL("../src/", import.meta.url));

test("frontend hosts do not import concrete modules or own module endpoints", async () => {
  const violations = [];
  for (const file of await readdir(root, { recursive: true })) {
    const relative = file.replaceAll("\\", "/");
    if (!/\.tsx?$/.test(relative) || relative.startsWith("generated/") || /\.(test|spec)\./.test(relative)) continue;
    const source = ts.createSourceFile(file, await readFile(path.join(root, file), "utf8"), ts.ScriptTarget.Latest, true);
    function visit(node) {
      if (ts.isStringLiteralLike(node) && (/^@(entities|channels)\//.test(node.text)
        || /(?:^|\/)entities\/[^/]+\//.test(node.text)
        || /(?:^|\/)channels\/[^/]+\/frontend\//.test(node.text)
        || /\/(?:api\/)?entity\/[a-z][a-z0-9_-]*(?:\/|$)/.test(node.text))) {
        violations.push(`${relative}: ${node.text}`);
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.deepEqual(violations, [], "Module implementations and endpoints belong in their owning directories");
});
