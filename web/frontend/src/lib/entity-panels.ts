/**
 * 实体面板注册表。
 *
 * 实体在 entities/<name>/panel.tsx 中编写自定义面板组件，
 * scripts/module-links.mjs 扫描生成 src/generated/entity-panels.ts
 * （懒加载表，经 @entities 别名直引实体目录真实路径——无软链、无提交
 * 残留，实体目录增删经 prebuild / dev watcher 自动重写接入表）。
 * 面板可按实体拆分为 entities/<name>/panels/ 子目录，panel.tsx 内用相对
 * 导入引用子组件；面板专属 i18n 放 panels/locales/{zh,en}.json，
 * 由 lib/entity-plugin-locales.ts 启动时 eager 注册（保留键 _registry 的
 * groups/configSections 映射声明工具页/配置中心分组名），panel.tsx 无需自行
 * registerPluginI18n；无面板的实体可只建 locales 目录自持组名翻译。
 *
 * 懒加载组件在模块加载时全量预建（lazy 只是包装、不触发导入）——
 * 渲染期创建组件会触发 React Compiler 的"Cannot create components during render"。
 */
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { panelLoaders } from "../generated/entity-panels";

// name → 懒加载组件（模块加载时一次性构建）
const panelRegistry = new Map<string, LazyExoticComponent<ComponentType>>();
for (const [name, loader] of Object.entries(panelLoaders)) {
  panelRegistry.set(name, lazy(loader));
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
