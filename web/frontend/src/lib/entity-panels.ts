/**
 * 实体面板注册表。
 *
 * 实体在 entities/<name>/panel.tsx 中编写自定义面板组件，
 * 通过 scripts/link_entity_panels.py（或 vite moduleFrontendsPlugin）软链接到
 * src/pages/entities/panels/，本文件通过 import.meta.glob 自动发现（构建时解析）。
 * 面板可按实体拆分为 entities/<name>/panels/ 子目录（整体软链为 panels/<name>/），
 * panel.tsx 内用相对导入引用子组件；面板专属 i18n 放 panels/locales/{zh,en}.json，
 * 由 lib/entity-plugin-locales.ts 启动时 eager 注册（保留键 _registry 的
 * groups/configSections 映射声明工具页/配置中心分组名），panel.tsx 无需自行
 * registerPluginI18n；无面板的实体可只建 locales 目录自持组名翻译。
 *
 * 新增实体面板后软链由 prebuild 钩子（scripts/module-links.mjs）自动同步，
 * dev 模式下由 vite moduleFrontendsPlugin 监听自动维护。
 *
 * 懒加载组件在模块加载时全量预建（lazy 只是包装、不触发导入）——
 * 渲染期创建组件会触发 React Compiler 的"Cannot create components during render"。
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";

// Vite 构建时解析：扫描 panels 目录下所有 .tsx 文件
const panelModules = import.meta.glob<{ default: ComponentType }>(
  "../pages/entities/panels/*.tsx",
);

// name → 懒加载组件（模块加载时一次性构建）
const panelRegistry = new Map<string, LazyExoticComponent<ComponentType>>();
for (const [path, loader] of Object.entries(panelModules)) {
  const match = path.match(/\/([^/]+)\.tsx$/);
  if (match?.[1]) {
    panelRegistry.set(match[1], lazy(loader));
  }
}

/**
 * 获取实体面板的懒加载组件。
 * @param name 实体 group 名（如 "web"、"sticker"）
 * @returns 懒加载组件，或 null（无自定义面板）
 */
export function getEntityPanel(name: string): LazyExoticComponent<ComponentType> | null {
  return panelRegistry.get(name) ?? null;
}

/** 列出所有有自定义面板的实体名。 */
export function listEntityPanels(): string[] {
  return [...panelRegistry.keys()];
}
