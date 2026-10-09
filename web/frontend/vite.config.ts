import { defineConfig, normalizePath, type Plugin } from "vite";
import react from "@vitejs/plugin-react-swc";
import tailwindcss from "@tailwindcss/postcss";
import path from "path";
import fs from "fs";
import { syncEntityPanels, syncModuleLinks } from "./scripts/module-links.mjs";

/**
 * Vite plugin: auto-discover and wire module frontends.
 *
 * Two wire domains (sync logic in scripts/module-links.mjs, single source
 * of truth, also run by the package.json prebuild hook before `tsc -b`):
 *
 * - Entity panels: entities/<name>/panel.tsx (+ panels/ subdir, locales)
 *   → codegen src/generated/entity-panels.ts + entity-panel-locales.ts
 *   (consumed via @entities alias, no symlinks, nothing to commit)
 * - Channel frontends: channels/<id>/frontend/ (whole dir)
 *   → codegen src/generated/channel-plugins.ts (consumed via @channels alias)
 *
 * - build: runs once at buildStart (prebuild 已先行同步，此处兜底)
 * - dev: fs.watch on entities/ + channels/ dirs, re-sync + full reload
 *   (模块源码在 vite root 之外，变更统一全量重载)
 */
function moduleFrontendsPlugin(): Plugin {
  const root = path.resolve(__dirname, "../..");
  const entitiesDir = path.join(root, "entities");
  const channelsDir = path.join(root, "channels");
  let dispose: (() => void) | undefined;

  const syncEntities = () => {
    const generated = syncEntityPanels();
    if (generated.length) {
      console.log(`[entity-panels] generated: ${generated.join(", ")}`);
    }
  };
  const syncChannels = () => {
    const linked = syncModuleLinks();
    if (linked.length) {
      console.log(`[module-frontends] linked: ${linked.join(", ")}`);
    }
  };

  return {
    name: "module-frontends",

    buildStart() {
      syncEntities();
      syncChannels();
    },

    configureServer(server) {
      dispose?.();
      let debounce: ReturnType<typeof setTimeout> | null = null;
      const watchers: fs.FSWatcher[] = [];
      const pending = new Set<() => void>();
      const watch = (dir: string, pattern: RegExp, sync: () => void) => {
        if (!fs.existsSync(dir)) return;
        watchers.push(fs.watch(dir, { recursive: true }, (_event, filename) => {
          if (!filename || !pattern.test(normalizePath(filename))) return;
          pending.add(sync);
          if (debounce) clearTimeout(debounce);
          debounce = setTimeout(() => {
            for (const update of pending) update();
            pending.clear();
            server.ws.send({ type: "full-reload" });
          }, 200);
        }));
      };
      watch(entitiesDir, /^[^/]+(?:\/(?:panel\.tsx|panels(?:\/.*)?))?$/, syncEntities);
      watch(channelsDir, /^[^/]+(?:\/frontend(?:\/.*)?)?$/, syncChannels);
      dispose = () => {
        if (debounce) clearTimeout(debounce);
        for (const watcher of watchers) watcher.close();
        pending.clear();
      };
    },
    closeBundle() { dispose?.(); },
  };
}

const projectRoot = normalizePath(path.resolve(__dirname, "../.."));

export default defineConfig({
  plugins: [moduleFrontendsPlugin(), react()],
  css: { postcss: { plugins: [tailwindcss()] } },
  base: "/webui/",
  resolve: {
    alias: {
      "@": normalizePath(path.resolve(__dirname, "./src")),
      // 实体面板源码真实路径（src/generated 的接入表与核心薄壳引用共用）
      "@entities": normalizePath(path.resolve(projectRoot, "entities")),
      "@channels": normalizePath(path.resolve(projectRoot, "channels")),
    },
    // 必须为 false（默认）：模块源码经 entities|channels/node_modules 解析桥
    // 引用 react 等依赖，preserveSymlinks 会把桥路径当成独立模块 id，打出
    // 第二个 react 副本（useContext 读 null 整站白屏）；统一 realpath 解析
    // 保证全应用单一 react 实例
    preserveSymlinks: false,
  },
  server: {
    port: 3000,
    watch: {
      ignored: [/[/\\]dist(?:\.next|\.old-[^/\\]+)?(?:[/\\]|$)/],
    },
    fs: {
      // 实体面板源码在 vite root 之外（仓库 entities/），dev 需放行仓库根
      allow: [projectRoot],
    },
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
          "i18n-vendor": ["i18next", "react-i18next", "i18next-browser-languagedetector"],
          "query-vendor": ["axios", "@tanstack/react-query"],
          "react-vendor": [
            "react",
            "react-dom",
            "react-router-dom",
            "react/jsx-runtime",
          ],
        },
      },
    },
  },
});
