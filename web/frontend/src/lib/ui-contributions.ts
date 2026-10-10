import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { LucideIcon } from "lucide-react";
import type { JsonObject } from "./types/json";
import type { NavigationGroup } from "./core-routes";
import { matchPath } from "react-router-dom";

export interface UiSlotProps {
  "workspace.tools": Record<string, never>;
  "dashboard.cards": Record<string, never>;
  "dashboard.actions": Record<string, never>;
  "data.tabs": Record<string, never>;
  "app.routes": Record<string, never>;
  "system.restart": Record<string, never>;
  "audio.overview.cards": Record<string, never>;
  "audio.identify.after": Record<string, never>;
  "audio.recording.actions": { path: string };
  "chat.message": { payload: JsonObject };
}
export type UiSlot = keyof UiSlotProps;
export interface UiText { ns: string; key: string }
interface ContributionBase {
  id: string;
  title: UiText;
  description?: UiText;
  order?: number;
  icon?: LucideIcon;
}
type SlotMetadata<S extends UiSlot> = S extends "app.routes" ? { path: string; group: NavigationGroup }
  : S extends "data.tabs" ? { tab: string }
  : S extends "chat.message" ? { messageType: string } : Record<never, never>;
export type UiContribution<S extends UiSlot = UiSlot> = { [K in S]: ContributionBase & SlotMetadata<K> & {
  slot: K;
  load: () => Promise<{ default: ComponentType<UiSlotProps[K]> }>;
} }[S];
type RegisteredContributions = { [K in UiSlot]: UiContribution<K> & {
  key: string; owner: string; Component: LazyExoticComponent<ComponentType<UiSlotProps[K]>>;
} }[UiSlot];
export type RegisteredContribution<S extends UiSlot = UiSlot> = Extract<RegisteredContributions, { slot: S }>;

const SLOTS: readonly UiSlot[] = ["workspace.tools", "dashboard.cards", "dashboard.actions", "data.tabs", "app.routes", "system.restart", "audio.identify.after", "audio.overview.cards", "audio.recording.actions", "chat.message"];

/** 声明模块向公共界面提供的内容，组件在挂载位置可见时才加载。 */
export function defineUiContributions(entries: readonly UiContribution[]): readonly UiContribution[] {
  const ids = new Set<string>();
  for (const entry of entries) {
    if (!/^[a-z][a-z0-9-]*$/.test(entry.id) || ids.has(entry.id)) throw new Error(`Invalid or duplicate UI contribution: ${entry.id}`);
    if (!SLOTS.includes(entry.slot)) throw new Error(`Unknown UI slot: ${entry.slot}`);
    if (!entry.title?.ns || !entry.title.key || typeof entry.load !== "function") throw new Error(`Incomplete UI contribution: ${entry.id}`);
    if (entry.order !== undefined && !Number.isFinite(entry.order)) throw new Error(`Invalid UI order: ${entry.id}`);
    if (entry.slot === "app.routes" && !/^\/[a-z][a-z0-9-]*(?:\/[a-z][a-z0-9-]*)*$/.test(entry.path)) throw new Error(`Invalid extension route: ${entry.path}`);
    if (entry.slot === "data.tabs" && !/^[a-z][a-z0-9-]*$/.test(entry.tab)) throw new Error(`Invalid extension tab: ${entry.tab}`);
    if (entry.slot === "chat.message" && !/^[a-z][a-z0-9.-]*$/.test(entry.messageType)) throw new Error(`Invalid message type: ${entry.messageType}`);
    ids.add(entry.id);
  }
  return entries;
}

/** 隔离各模块的清单加载错误，并按稳定顺序组装静态组件引用。 */
export async function loadUiContributions(loaders: Record<string, () => Promise<{ default: readonly UiContribution[] }>>, reserved: readonly string[] = []): Promise<RegisteredContribution[]> {
  const modules = await Promise.all(Object.entries(loaders).map(async ([owner, load]) => {
    try {
      return defineUiContributions((await load()).default).map((entry) => {
        const identity = { owner, key: `${owner}/${entry.id}` };
        if (entry.slot === "chat.message") return { ...entry, ...identity, Component: lazy(entry.load) };
        if (entry.slot === "audio.recording.actions") return { ...entry, ...identity, Component: lazy(entry.load) };
        return { ...entry, ...identity, Component: lazy(entry.load) };
      });
    } catch (error) {
      console.error(`[ui-contributions] ${owner}`, error);
      return [];
    }
  }));
  const claimed = new Set(reserved);
  return modules.flat().sort((a, b) => (a.order ?? 100) - (b.order ?? 100) || a.key.localeCompare(b.key, "en")).filter((entry) => {
    const target = entry.slot === "app.routes" ? entry.path : entry.slot === "data.tabs" ? entry.tab : entry.slot === "chat.message" ? entry.messageType : entry.slot === "system.restart" ? "default" : undefined;
    if (!target) return true;
    const key = `${entry.slot}:${target}`;
    const overlapsRoute = entry.slot === "app.routes" && [...claimed].some((path) => path.startsWith("app.routes:") && matchPath({ path: path.slice("app.routes:".length), end: true }, entry.path));
    if (claimed.has(key) || overlapsRoute) { console.error(`[ui-contributions] Duplicate target ${key}: ${entry.owner}`); return false; }
    claimed.add(key);
    return true;
  });
}
