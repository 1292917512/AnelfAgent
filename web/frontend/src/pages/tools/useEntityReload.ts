import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { entitiesApi, toolsApi } from "@/lib/api";
import { toast } from "@/components/ui";

/** 工具注册变化后同步目录、详情与标签；不重建正在编辑的面板。 */
export async function refreshToolCatalog(client: QueryClient): Promise<void> {
  await Promise.all(["tools-grouped", "entity-detail", "entities", "tool-tags", "unified-tags", "plugins"].map((key) =>
    client.invalidateQueries({ queryKey: [key] })));
}

/** 单实体与全量重载共用反馈和缓存失效，后台跳过或部分失败不显示为成功。 */
export function useEntityReload() {
  const { t } = useTranslation("tools");
  const client = useQueryClient();
  return useMutation({
    mutationFn: (module: string | null) => (module ? entitiesApi.reload(module) : toolsApi.reload()).then((response) => response.data),
    onSuccess: async (result, module) => {
      if (result.skipped) toast.info(t("reloadBusy"));
      else if (result.failed.length) toast.error(t("reloadFailed", { names: result.failed.join(", ") }));
      else toast.success(module ? t("reloadSuccess", { name: module }) : t("reloadAllSuccess", { count: result.added.length + result.removed.length + result.reloaded.length }));
      await refreshToolCatalog(client);
    },
  });
}
