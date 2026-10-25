import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { adaptersApi } from "@/lib/api";
import { refreshToolCatalog } from "@/lib/tool-catalog";
import { toast } from "@/components/ui";

/** 频道重载后更新状态、配置和工具；失败也刷新，以呈现恢复后的实际状态。 */
export function useChannelReload() {
  const { t } = useTranslation("channels");
  const client = useQueryClient();
  return useMutation({
    mutationFn: (key: string | null) => adaptersApi.reload(key).then((response) => response.data),
    onSuccess: (result, key) => {
      if (result.skipped) toast.info(t("reloadBusy"));
      else if (result.failed.length) toast.error(t("reloadFailed", { names: result.failed.join(", ") }));
      else toast.success(key ? t("reloadSuccess", { name: key }) : t("reloadAllSuccess", { count: result.added.length + result.removed.length + result.reloaded.length }));
    },
    onSettled: () => Promise.all([
      client.invalidateQueries({ queryKey: ["adapters"] }),
      client.invalidateQueries({ queryKey: ["configMeta"] }),
      refreshToolCatalog(client),
    ]),
  });
}
