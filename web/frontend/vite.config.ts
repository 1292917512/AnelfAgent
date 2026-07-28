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

    // 清理无效链接
    for (const f of fs.readdirSync(panelsDir)) {
      if (!f.endsWith(".tsx")) continue;
      const fp = path.join(panelsDir, f);
      if (fs.lstatSync(fp).isSymbolicLink() && !fs.existsSync(fp)) {
        fs.unlinkSync(fp);
      }
    }

    // 扫描实体目录
    if (!fs.existsSync(entitiesDir)) return linked;
    for (const name of fs.readdirSync(entitiesDir).sort()) {
      if (name.startsWith("_") || name.startsWith(".")) continue;
      const panelSrc = path.join(entitiesDir, name, "panel.tsx");
      if (!fs.existsSync(panelSrc)) continue;

      const linkPath = path.join(panelsDir, `${name}.tsx`);
      const rel = path.relative(panelsDir, panelSrc);

      if (fs.existsSync(linkPath)) {
        if (fs.lstatSync(linkPath).isSymbolicLink() && fs.readlinkSync(linkPath) === rel) {
          linked.push(name);
          continue;
        }
        fs.unlinkSync(linkPath);
      }
      fs.symlinkSync(rel, linkPath);
      linked.push(name);
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
          const before = new Set(
            fs.readdirSync(panelsDir).filter((f) => f.endsWith(".tsx")),
          );
          syncLinks();
          const after = new Set(
            fs.readdirSync(panelsDir).filter((f) => f.endsWith(".tsx")),
          );
          // 面板增删时触发全量重载（import.meta.glob 需要重新扫描）
          const changed =
            before.size !== after.size ||
            [...before].some((f) => !after.has(f));
          if (changed) {
            console.log("[entity-panels] panels changed, reloading...");
            server.ws.send({ type: "full-reload" });
          }
        }, 200);
      };

      fs.watch(entitiesDir, { recursive: true }, (event, filename) => {
        if (filename?.endsWith("panel.tsx")) handleChange();
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
