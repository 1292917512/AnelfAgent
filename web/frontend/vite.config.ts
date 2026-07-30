import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import fs from "fs";

/**
 * Vite plugin: auto-discover and link entity panels.
 *
 * Scans entities/<name>/panel.tsx, creates/cleans symlinks in
 * src/pages/entities/panels/ so import.meta.glob can find them.
 * Entities may split panel code into entities/<name>/panels/ — the whole
 * directory is symlinked as panels/<name>/ so panel.tsx can use relative
 * imports (preserveSymlinks keeps everything under src/, no extra alias).
 *
 * - build: runs once at buildStart
 * - dev: fs.watch on entities/ dir, auto-maintains links + triggers HMR
 */
function entityPanelsPlugin(): Plugin {
  const root = path.resolve(__dirname, "../..");
  const entitiesDir = path.join(root, "entities");
  const panelsDir = path.resolve(__dirname, "src/pages/entities/panels");

  function syncLinks(): string[] {
    fs.mkdirSync(panelsDir, { recursive: true });
    const linked: string[] = [];

    // 期望的软链集合：链接名 → 相对目标
    const expected = new Map<string, string>();
    if (fs.existsSync(entitiesDir)) {
      for (const name of fs.readdirSync(entitiesDir).sort()) {
        if (name.startsWith("_") || name.startsWith(".")) continue;
        const panelSrc = path.join(entitiesDir, name, "panel.tsx");
        if (!fs.existsSync(panelSrc)) continue;
        expected.set(`${name}.tsx`, path.relative(panelsDir, panelSrc));
        // 面板拆分子目录：entities/<name>/panels/ → panels/<name>/
        const subDir = path.join(entitiesDir, name, "panels");
        if (fs.existsSync(subDir) && fs.statSync(subDir).isDirectory()) {
          expected.set(name, path.relative(panelsDir, subDir));
        }
        linked.push(name);
      }
    }

    // 清理：非期望集合或目标已失效的软链
    for (const f of fs.readdirSync(panelsDir)) {
      const fp = path.join(panelsDir, f);
      if (!fs.lstatSync(fp).isSymbolicLink()) continue;
      if (!expected.has(f) || !fs.existsSync(fp)) {
        fs.rmSync(fp, { force: true, recursive: true });
      }
    }

    // 创建/更新软链
    for (const [f, rel] of expected) {
      const fp = path.join(panelsDir, f);
      if (fs.existsSync(fp)) {
        if (fs.lstatSync(fp).isSymbolicLink() && fs.readlinkSync(fp) === rel) {
          continue;
        }
        fs.rmSync(fp, { force: true, recursive: true });
      }
      const isDir = fs.statSync(path.resolve(panelsDir, rel)).isDirectory();
      fs.symlinkSync(rel, fp, isDir ? "dir" : "file");
    }
    return linked;
  }

  return {
    name: "entity-panels",

    buildStart() {
      const linked = syncLinks();
      if (linked.length) {
        console.log(`[entity-panels] linked: ${linked.join(", ")}`);
      }
    },

    configureServer(server) {
      // dev 模式：监听 entities/ 目录变化
      if (!fs.existsSync(entitiesDir)) return;

      let debounce: ReturnType<typeof setTimeout> | null = null;
      const handleChange = () => {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => {
          syncLinks();
          // 实体面板源码在 root 之外，vite 默认监听不到，统一全量重载
          server.ws.send({ type: "full-reload" });
        }, 200);
      };

      fs.watch(entitiesDir, { recursive: true }, (event, filename) => {
        if (filename?.endsWith(".tsx")) handleChange();
      });
    },
  };
}

export default defineConfig({
  plugins: [entityPanelsPlugin(), react(), tailwindcss()],
  base: "/webui/",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
    preserveSymlinks: true,
  },
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8091",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
});
