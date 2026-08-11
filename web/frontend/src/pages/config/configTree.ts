import type { ConfigMetaGroup, ConfigMetaItem } from "@/lib/api";

export interface ConfigSectionNode {
  /** 完整分组 key（module/section） */
  group: string;
  items: ConfigMetaItem[];
}

export interface ConfigModuleNode {
  /** 模块 key（group 的第一段，无 `/` 时归入 other） */
  module: string;
  sections: ConfigSectionNode[];
}

/** 将扁平 groups 组装为 模块 → 分组 两级树，按 i18n 资源 key 顺序排序（未列出排最后）。 */
export function buildConfigTree(
  groups: ConfigMetaGroup[],
  moduleOrder: string[],
  sectionOrder: string[],
): ConfigModuleNode[] {
  const modules = new Map<string, ConfigSectionNode[]>();
  for (const g of groups) {
    const module = g.group.includes("/") ? (g.group.split("/")[0] ?? "other") : "other";
    const sections = modules.get(module) ?? [];
    sections.push({ group: g.group, items: g.items });
    modules.set(module, sections);
  }

  const byOrder = (order: string[]) => (a: string, b: string) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  };

  return [...modules.entries()]
    .sort((a, b) => byOrder(moduleOrder)(a[0], b[0]))
    .map(([module, sections]) => ({
      module,
      sections: [...sections].sort((a, b) => byOrder(sectionOrder)(a.group, b.group)),
    }));
}

/** 在树中定位配置项所属分组 */
export function findGroupOfKey(tree: ConfigModuleNode[], key: string): string | null {
  for (const m of tree) {
    for (const s of m.sections) {
      if (s.items.some((i) => i.key === key)) return s.group;
    }
  }
  return null;
}

/** 跨全部分组检索（key / 描述，大小写不敏感） */
export function searchConfigItems(
  tree: ConfigModuleNode[],
  query: string,
): ConfigSectionNode[] {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  const results: ConfigSectionNode[] = [];
  for (const m of tree) {
    for (const s of m.sections) {
      const items = s.items.filter(
        (i) =>
          i.key.toLowerCase().includes(q) || i.description.toLowerCase().includes(q),
      );
      if (items.length > 0) results.push({ group: s.group, items });
    }
  }
  return results;
}
