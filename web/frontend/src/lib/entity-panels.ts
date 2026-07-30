/**
 * 实体面板注册表。
 *
 * 实体在 entities/<name>/panel.tsx 中编写自定义面板组件，
 * 通过 scripts/link_entity_panels.py（或 vite entity-panels 插件）软链接到
 * src/pages/entities/panels/，本文件通过 import.meta.glob 自动发现（构建时解析）。
 * 面板可按实体拆分为 entities/<name>/panels/ 子目录（整体软链为 panels/<name>/），
 * panel.tsx 内用相对导入引用子组件。
 *
 * 新增实体面板后需重新执行 link_entity_panels.py 并重启 dev server。
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";

// Vite 构建时解析：扫描 panels 目录下所有 .tsx 文件
const panelModules = import.meta.glob<{ default: ComponentType }>(
  "../pages/entities/panels/*.tsx",
);

// 构建懒加载组件映射
const panelCache = new Map<string, LazyExoticComponent<ComponentType>>();

/**
 * 获取实体面板的懒加载组件。
 * @param name 实体 group 名（如 "web"、"sticker"）
 * @returns 懒加载组件，或 null（无自定义面板）
 */
export function getEntityPanel(name: string): LazyExoticComponent<ComponentType> | null {
  if (panelCache.has(name)) {
    return panelCache.get(name)!;
  }
  const path = `../pages/entities/panels/${name}.tsx`;
  const loader = panelModules[path];
  if (!loader) return null;
  const comp = lazy(loader);
  panelCache.set(name, comp);
  return comp;
}

/** 列出所有有自定义面板的实体名。 */
export function listEntityPanels(): string[] {
  return Object.keys(panelModules).map((p) => {
    const match = p.match(/\/([^/]+)\.tsx$/);
    return match?.[1] ?? "";
  }).filter(Boolean);
}
