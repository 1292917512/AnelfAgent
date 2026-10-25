import type { QueryClient } from "@tanstack/react-query";

/** 工具注册变化后同步目录、详情与标签；保留正在编辑的面板和草稿。 */
export async function refreshToolCatalog(client: QueryClient): Promise<void> {
  await Promise.all(["tools-grouped", "entity-detail", "entities", "tool-tags", "unified-tags", "plugins", "channelTools"].map((key) =>
    client.invalidateQueries({ queryKey: [key] })));
}
