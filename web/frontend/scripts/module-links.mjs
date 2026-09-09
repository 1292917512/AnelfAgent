/**
 * 模块前端软链接同步（单一事实源）。
 *
 * 两个消费方：
 * - package.json prebuild：`tsc -b` 之前自愈软链（同事增删 entities/channels
 *   模块目录后，已提交的软链可能悬空或缺失，不同步则 tsc 直接失败）
 * - vite.config.ts moduleFrontendsPlugin：buildStart / dev 监听复用同逻辑
 *
 * 链接域：
 * - 实体面板：entities/<name>/panel.tsx（+ panels/ 子目录）
 *   → src/pages/entities/panels/
 * - 频道前端：channels/<id>/frontend/（整目录，需含 index.ts）
 *   → src/plugins/channels/<id>/
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(frontendDir, "../..");
const entitiesDir = path.join(projectRoot, "entities");
const channelsDir = path.join(projectRoot, "channels");
const panelsDir = path.join(frontendDir, "src/pages/entities/panels");
const channelPluginsDir = path.join(frontendDir, "src/plugins/channels");

function syncDir(targetDir, expected) {
  fs.mkdirSync(targetDir, { recursive: true });
  // 清理：非期望集合或目标已失效的软链
  for (const f of fs.readdirSync(targetDir)) {
    const fp = path.join(targetDir, f);
    if (!fs.lstatSync(fp).isSymbolicLink()) continue;
    if (!expected.has(f) || !fs.existsSync(fp)) {
      fs.rmSync(fp, { force: true, recursive: true });
    }
  }
  // 创建/更新软链
  for (const [f, rel] of expected) {
    const fp = path.join(targetDir, f);
    if (fs.existsSync(fp)) {
      if (fs.lstatSync(fp).isSymbolicLink() && fs.readlinkSync(fp) === rel) {
        continue;
      }
      fs.rmSync(fp, { force: true, recursive: true });
    }
    const isDir = fs.statSync(path.resolve(targetDir, rel)).isDirectory();
    fs.symlinkSync(rel, fp, isDir ? "dir" : "file");
  }
}

/** 同步全部模块前端软链，返回已链接模块列表（如 ["entity:web", "channel:qq"]）。 */
export function syncModuleLinks() {
  const linked = [];

  // 实体面板：entities/<name>/panel.tsx + panels/ 子目录
  const entityExpected = new Map();
  if (fs.existsSync(entitiesDir)) {
    for (const name of fs.readdirSync(entitiesDir).sort()) {
      if (name.startsWith("_") || name.startsWith(".")) continue;
      const panelSrc = path.join(entitiesDir, name, "panel.tsx");
      if (!fs.existsSync(panelSrc)) continue;
      entityExpected.set(`${name}.tsx`, path.relative(panelsDir, panelSrc));
      const subDir = path.join(entitiesDir, name, "panels");
      if (fs.existsSync(subDir) && fs.statSync(subDir).isDirectory()) {
        entityExpected.set(name, path.relative(panelsDir, subDir));
      }
      linked.push(`entity:${name}`);
    }
  }
  syncDir(panelsDir, entityExpected);

  // 频道前端：channels/<id>/frontend/ 整目录
  const channelExpected = new Map();
  if (fs.existsSync(channelsDir)) {
    for (const name of fs.readdirSync(channelsDir).sort()) {
      if (name.startsWith("_") || name.startsWith(".")) continue;
      const frontendDir = path.join(channelsDir, name, "frontend");
      if (!fs.existsSync(frontendDir) || !fs.statSync(frontendDir).isDirectory()) continue;
      if (!fs.existsSync(path.join(frontendDir, "index.ts"))) continue;
      channelExpected.set(name, path.relative(channelPluginsDir, frontendDir));
      linked.push(`channel:${name}`);
    }
  }
  syncDir(channelPluginsDir, channelExpected);

  return linked;
}

// CLI：node scripts/module-links.mjs（prebuild 钩子 / 手工执行）
if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const linked = syncModuleLinks();
  if (linked.length) {
    console.log(`[module-links] linked: ${linked.join(", ")}`);
  }
}
