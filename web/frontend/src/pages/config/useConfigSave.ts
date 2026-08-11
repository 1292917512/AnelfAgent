import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { configMetaApi } from "@/lib/api";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { toast } from "@/stores/toast-store";

/**
 * 配置项保存 hook：统一 mutation + configMeta 缓存失效 + 成功反馈 + 失败提示。
 * 返回 save（提交值）、saving（进行中）、saved（成功短暂反馈）。
 */
export function useConfigSave(key: string) {
  const { t } = useTranslation("config");
  const queryClient = useQueryClient();
  const [saved, triggerSaved] = useCopyFeedback(1500);

  const mutation = useMutation({
    mutationFn: (value: unknown) => configMetaApi.save(key, value),
    onSuccess: () => {
      triggerSaved();
      queryClient.invalidateQueries({ queryKey: ["configMeta"] });
    },
    onError: (err: Error) => {
      toast.error(t("saveFailed", { message: err.message }));
    },
  });

  return { save: mutation.mutate, saving: mutation.isPending, saved };
}
