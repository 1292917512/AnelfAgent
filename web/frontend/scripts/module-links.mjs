/**
 * 模块前端接入（单一事实源）——实体面板代码生成 + 频道前端软链。
 *
 * 两个消费方：
 * - package.json prebuild：`tsc -b` 之前自愈（同事增删 entities/channels
 *   模块目录后，生成文件/软链可能过期，不同步则 tsc 直接失败）
 * - vite.config.ts moduleFrontendsPlugin：buildStart / dev 监听复用同逻辑
 *
 * 接入域：
 * - 实体面板：entities/<name>/panel.tsx（+ panels/ 子目录、panels/locales/）
 *   → 代码生成 src/generated/entity-panels.ts（面板懒加载表）与
 *   src/generated/entity-panel-locales.ts（locale eager 表），面板源码
 *   经 @entities 别名（vite alias + tsconfig paths）以真实路径被
 *   tsc/vite/eslint 直接消费——无软链、无提交残留，实体目录增删即面板
 *   插拔。locale-only 实体（无 panel.tsx 但有 panels/locales/）同样收录
 *   locale 表（自持组名翻译）。生成文件 gitignored，内容不变不写盘
 *   （避免 mtime 漂移触发无谓重建）。
 * - 频道前端：channels/<id>/frontend/（整目录，需含 index.ts）
 *   → 软链 src/plugins/channels/<id>/（软链须提交 git）
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectRoot = path.resolve(frontendDir, "../..");
const entitiesDir = path.join(projectRoot, "entities");
const channelsDir = path.join(projectRoot, "channels");
const generatedDir = path.join(frontendDir, "src/generated");
const channelPluginsDir = path.join(frontendDir, "src/plugins/channels");

const PANELS_FILE = path.join(generatedDir, "entity-panels.ts");
const LOCALES_FILE = path.join(generatedDir, "entity-panel-locales.ts");

/** 扫描实体目录：面板与 locale 条目（按名称排序保证输出稳定）。 */
function scanEntities() {
  const panels = [];
  const locales = [];
  if (!fs.existsSync(entitiesDir)) return { panels, locales };
  for (const name of fs.readdirSync(entitiesDir).sort()) {
    if (name.startsWith("_") || name.startsWith(".")) continue;
    const entityDir = path.join(entitiesDir, name);
    if (!fs.statSync(entityDir).isDirectory()) continue;
    if (fs.existsSync(path.join(entityDir, "panel.tsx"))) {
      panels.push(name);
    }
    const localesDir = path.join(entityDir, "panels", "locales");
    if (fs.existsSync(localesDir)) {
      for (const lang of ["zh", "en"]) {
        if (fs.existsSync(path.join(localesDir, `${lang}.json`))) {
          locales.push({ name, lang });
        }
      }
    }
  }
  return { panels, locales };
}

function renderPanelsCode(panels) {
  const lines = panels.map(
    (name) => `  ${JSON.stringify(name)}: () => import("@entities/${name}/panel"),`,
  );
  return `/**
 * 实体面板懒加载表（代码生成，勿手改）。
 * 由 scripts/module-links.mjs 扫描 entities/<name>/panel.tsx 生成，
 * 新增/删除实体面板经 prebuild 或 dev watcher 自动重写本文件。
 */
import type { ComponentType } from "react";

export const panelLoaders: Record<
  string,
  () => Promise<{ default: ComponentType }>
> = {
${lines.join("\n")}
};
`;
}

function renderLocalesCode(locales) {
  const imports = [];
  const entries = [];
  locales.forEach(({ name, lang }, i) => {
    imports.push(
      `import locale${i} from "@entities/${name}/panels/locales/${lang}.json";`,
    );
    entries.push(
      `  { name: ${JSON.stringify(name)}, lang: ${JSON.stringify(lang)}, bundle: locale${i} },`,
    );
  });
  return `/**
 * 实体面板 locale eager 表（代码生成，勿手改）。
 * 由 scripts/module-links.mjs 扫描 entities/<name>/panels/locales/{zh,en}.json
 * 生成；locale-only 实体（无 panel.tsx）同样收录。
 */
${imports.join("\n")}

export interface PanelLocaleEntry {
  name: string;
  lang: "zh" | "en";
  bundle: Record<string, unknown>;
}

export const panelLocales: PanelLocaleEntry[] = [
${entries.join("\n")}
];
`;
}

/** 内容变化才写盘（避免无谓的 mtime 漂移触发 HMR/重建）。 */
function writeIfChanged(filePath, content) {
  if (fs.existsSync(filePath) && fs.readFileSync(filePath, "utf8") === content) {
    return false;
  }
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, "utf8");
  return true;
}

/** 模块源码的 node 模块解析桥：<dir>/node_modules → web/frontend/node_modules。
 *
 * 实体面板（真实路径引用）与频道前端（软链，vite 已关闭 preserveSymlinks、
 * 按 realpath 解析）的裸导入（react、lucide-react 等）都会从模块目录向上
 * 查找 node_modules——仓库根没有，必须在模块根下放一个指向前端依赖树的
 * 软链（gitignored，prebuild/dev 自愈，一次创建全局生效）。统一 realpath
 * 解析保证全应用只有一份 react 实例（preserveSymlinks 下桥路径会形成
 * 第二个 react 副本，useContext 读 null 整站白屏）。
 */
function ensureNodeModulesBridge(dir) {
  const link = path.join(dir, "node_modules");
  const rel = path.relative(dir, path.join(frontendDir, "node_modules"));
  if (fs.existsSync(link)) {
    if (fs.lstatSync(link).isSymbolicLink() && fs.readlinkSync(link) === rel) return;
    fs.rmSync(link, { force: true, recursive: true });
  }
  if (!fs.existsSync(path.join(frontendDir, "node_modules"))) return;
  fs.symlinkSync(rel, link, "dir");
}

/** 生成实体面板接入文件（src/generated/），返回收录的实体名列表。 */
export function syncEntityPanels() {
  ensureNodeModulesBridge(entitiesDir);
  const { panels, locales } = scanEntities();
  writeIfChanged(PANELS_FILE, renderPanelsCode(panels));
  writeIfChanged(LOCALES_FILE, renderLocalesCode(locales));
  return [...new Set([...panels, ...locales.map((l) => l.name)])];
}

/** 对账式同步目标目录软链（清理非期望/失效链接，创建缺失链接）。 */
function syncDir(targetDir, expected) {
  fs.mkdirSync(targetDir, { recursive: true });
  for (const f of fs.readdirSync(targetDir)) {
    const fp = path.join(targetDir, f);
    if (!fs.lstatSync(fp).isSymbolicLink()) continue;
    if (!expected.has(f) || !fs.existsSync(fp)) {
      fs.rmSync(fp, { force: true, recursive: true });
    }
  }
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

/** 同步频道前端软链（channels/<id>/frontend/ 整目录），返回已链接频道列表。 */
export function syncModuleLinks() {
  ensureNodeModulesBridge(channelsDir);
  const linked = [];
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
  const entities = syncEntityPanels();
  if (entities.length) {
    console.log(`[entity-panels] generated: ${entities.join(", ")}`);
  }
  const linked = syncModuleLinks();
  if (linked.length) {
    console.log(`[module-links] linked: ${linked.join(", ")}`);
  }
}
