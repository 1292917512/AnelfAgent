import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import fs from "fs";

/**
 * Vite plugin: auto-discover and link module frontends.
 *
 * Two link domains (same symlink mechanism, committed to git so `tsc -b`
 * passes before vite buildStart):
 *
 * - Entity panels: entities/<name>/panel.tsx (+ panels/ subdir)
 *   → src/pages/entities/panels/
 * - Channel frontends: channels/<id>/frontend/ (whole dir)
 *   → src/plugins/channels/<id>/
 *
 * preserveSymlinks keeps everything under src/ (no extra alias / fs.allow).
 *
 * - build: runs once at buildStart
 * - dev: fs.watch on entities/ + channels/ dirs, auto-maintains links + HMR
 */
function moduleFrontendsPlugin(): Plugin {
  const root = path.resolve(__dirname, "../..");
  const entitiesDir = path.join(root, "entities");
  const channelsDir = path.join(root, "channels");
  const panelsDir = path.resolve(__dirname, "src/pages/entities/panels");
  const channelPluginsDir = path.resolve(__dirname, "src/plugins/channels");

  function syncDir(targetDir: string, expected: Map<string, string>): void {
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

  function syncLinks(): string[] {
    const linked: string[] = [];

    // 实体面板：entities/<name>/panel.tsx + panels/ 子目录
    const entityExpected = new Map<string, string>();
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
    const channelExpected = new Map<string, string>();
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

  return {
    name: "module-frontends",

    buildStart() {
      const linked = syncLinks();
      if (linked.length) {
        console.log(`[module-frontends] linked: ${linked.join(", ")}`);
      }
    },

    configureServer(server) {
      let debounce: ReturnType<typeof setTimeout> | null = null;
      const handleChange = () => {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => {
          syncLinks();
          // 模块前端源码在 vite root 之外，默认监听不到，统一全量重载
          server.ws.send({ type: "full-reload" });
        }, 200);
      };

      for (const dir of [entitiesDir, channelsDir]) {
        if (!fs.existsSync(dir)) continue;
        fs.watch(dir, { recursive: true }, (event, filename) => {
          if (filename && /\.(tsx?|json)$/.test(filename)) handleChange();
        });
      }
    },
  };
}

export default defineConfig({
  plugins: [moduleFrontendsPlugin(), react(), tailwindcss()],
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
