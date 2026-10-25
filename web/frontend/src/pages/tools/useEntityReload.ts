import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { entitiesApi, toolsApi } from "@/lib/api";
import { toast } from "@/components/ui";
import { refreshToolCatalog } from "@/lib/tool-catalog";

export { refreshToolCatalog } from "@/lib/tool-catalog";

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
