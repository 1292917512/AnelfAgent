/**
 * 实体面板注册表。
 *
 * 实体在 entities/<name>/panel.tsx 中编写自定义面板组件，
 * 通过 scripts/link_entity_panels.py 软链接到 src/pages/entities/panels/，
 * 本文件通过 import.meta.glob 自动发现（Vite 构建时解析）。
 *
 * 新增实体面板后需重新执行 link_entity_panels.py 并重启 dev server。
 *
 * 面板查找支持两种命名：
 * 1. 精确匹配：group 名 = 目录名（如 "web" → web.tsx）
 * 2. 后缀剥离：group 名去下划线后缀（如 "file_share" → share.tsx）
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
 * @param name 实体 group 名（如 "web"、"sticker"、"file_share"）
 * @returns 懒加载组件，或 null（无自定义面板）
 */
export function getEntityPanel(name: string): LazyExoticComponent<ComponentType> | null {
  if (panelCache.has(name)) {
    return panelCache.get(name)!;
  }

  // 尝试多种命名变体
  const candidates = [name];
  // file_share → share（去掉最后一个下划线后缀）
  const lastUnderscore = name.lastIndexOf("_");
  if (lastUnderscore > 0) {
    candidates.push(name.slice(0, lastUnderscore));
  }

  for (const candidate of candidates) {
    const path = `../pages/entities/panels/${candidate}.tsx`;
    const loader = panelModules[path];
    if (loader) {
      const comp = lazy(loader);
      panelCache.set(name, comp);
      return comp;
    }
  }
  return null;
}

/** 列出所有有自定义面板的实体名。 */
export function listEntityPanels(): string[] {
  return Object.keys(panelModules).map((p) => {
    const match = p.match(/\/([^/]+)\.tsx$/);
    return match?.[1] ?? "";
  }).filter(Boolean);
}
