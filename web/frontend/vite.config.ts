import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import fs from "fs";
import { syncModuleLinks } from "./scripts/module-links.mjs";

/**
 * Vite plugin: auto-discover and link module frontends.
 *
 * Two link domains (same symlink mechanism, committed to git):
 *
 * - Entity panels: entities/<name>/panel.tsx (+ panels/ subdir)
 *   → src/pages/entities/panels/
 * - Channel frontends: channels/<id>/frontend/ (whole dir)
 *   → src/plugins/channels/<id>/
 *
 * Sync logic lives in scripts/module-links.mjs (single source of truth,
 * also run by the package.json prebuild hook before `tsc -b`).
 *
 * preserveSymlinks keeps everything under src/ (no extra alias / fs.allow).
 *
 * - build: runs once at buildStart (prebuild 已先行同步，此处兜底)
 * - dev: fs.watch on entities/ + channels/ dirs, auto-maintains links + HMR
 */
function moduleFrontendsPlugin(): Plugin {
  const root = path.resolve(__dirname, "../..");
  const entitiesDir = path.join(root, "entities");
  const channelsDir = path.join(root, "channels");

  return {
    name: "module-frontends",

    buildStart() {
      const linked = syncModuleLinks();
      if (linked.length) {
        console.log(`[module-frontends] linked: ${linked.join(", ")}`);
      }
    },

    configureServer(server) {
      let debounce: ReturnType<typeof setTimeout> | null = null;
      const handleChange = () => {
        if (debounce) clearTimeout(debounce);
        debounce = setTimeout(() => {
          syncModuleLinks();
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
