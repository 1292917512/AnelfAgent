import { useMutation, useQueryClient } from "@tanstack/react-query";
import { configMetaApi } from "@/lib/api";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";

export function useConfigSave(key: string) {
  const queryClient = useQueryClient();
  const [saved, triggerSaved] = useCopyFeedback(1500);
  const mutation = useMutation({
    scope: { id: `config:${key}` },
    mutationFn: (value: unknown) => configMetaApi.save(key, value),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["configMeta"] }),
        queryClient.invalidateQueries({ queryKey: ["entity-detail"] }),
      ]);
      triggerSaved();
    },
  });
  return { save: mutation.mutate, saving: mutation.isPending, saved, error: mutation.error };
}
