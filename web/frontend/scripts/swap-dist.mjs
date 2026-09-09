/**
 * 构建产物原子切换：vite 输出到 dist.next，构建全部完成后才交换为 dist。
 *
 * 后端 web/server.py 每次请求实时从磁盘读 dist（index.html 禁缓存逐请求读盘），
 * 此前 vite 直接写 dist 且 emptyOutDir 在构建开始时清空整个目录——运行中的服务
 * 在整个构建窗口内无产物可服务（页面 500/黑屏）。先建后换使旧版本全程可用，
 * 切换窗口仅为两次 rename 之间（微秒级）。
 *
 * 用法：node scripts/swap-dist.mjs（package.json build 尾部调用）
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dist = path.join(frontendDir, "dist");
const next = path.join(frontendDir, "dist.next");
const old = path.join(frontendDir, `dist.old-${process.pid}`);

if (!fs.existsSync(path.join(next, "index.html"))) {
  console.error("[swap-dist] dist.next/index.html 不存在，中止交换（旧 dist 保持原样）");
  process.exit(1);
}

// 清理历史交换中断残留的 dist.old-*（正常流程不会存在）
for (const f of fs.readdirSync(frontendDir)) {
  if (f.startsWith("dist.old-")) {
    fs.rmSync(path.join(frontendDir, f), { recursive: true, force: true });
  }
}

if (fs.existsSync(dist)) fs.renameSync(dist, old);
fs.renameSync(next, dist);
fs.rmSync(old, { recursive: true, force: true });
console.log("[swap-dist] dist 已切换为新构建产物");
