import { lazy, type ComponentType, type LazyExoticComponent } from "react";

export type UiSlot = "workspace.tools" | "dashboard.cards";
export interface UiText { ns: string; key: string }
export interface UiContribution {
  id: string;
  slot: UiSlot;
  title: UiText;
  description?: UiText;
  order?: number;
  load: () => Promise<{ default: ComponentType }>;
}
export interface RegisteredContribution extends UiContribution {
  key: string;
  owner: string;
  Component: LazyExoticComponent<ComponentType>;
}

/** 声明模块向公共界面提供的内容，组件在挂载位置可见时才加载。 */
export function defineUiContributions(entries: readonly UiContribution[]): readonly UiContribution[] {
  const ids = new Set<string>();
  for (const entry of entries) {
    if (!/^[a-z][a-z0-9-]*$/.test(entry.id) || ids.has(entry.id)) throw new Error(`Invalid or duplicate UI contribution: ${entry.id}`);
    if (!["workspace.tools", "dashboard.cards"].includes(entry.slot)) throw new Error(`Unknown UI slot: ${entry.slot}`);
    if (!entry.title?.ns || !entry.title.key || typeof entry.load !== "function") throw new Error(`Incomplete UI contribution: ${entry.id}`);
    if (entry.order !== undefined && !Number.isFinite(entry.order)) throw new Error(`Invalid UI order: ${entry.id}`);
    ids.add(entry.id);
  }
  return entries;
}

/** 隔离各模块的清单加载错误，并按稳定顺序组装静态组件引用。 */
export async function loadUiContributions(loaders: Record<string, () => Promise<{ default: readonly UiContribution[] }>>): Promise<RegisteredContribution[]> {
  const modules = await Promise.all(Object.entries(loaders).map(async ([owner, load]) => {
    try {
      return defineUiContributions((await load()).default).map((entry) => ({
        ...entry, owner, key: `${owner}/${entry.id}`, Component: lazy(entry.load),
      }));
    } catch (error) {
      console.error(`[ui-contributions] ${owner}`, error);
      return [];
    }
  }));
  return modules.flat().sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.key.localeCompare(b.key, "en"));
}
