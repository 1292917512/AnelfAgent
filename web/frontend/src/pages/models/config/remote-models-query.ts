import { queryOptions } from "@tanstack/react-query";
import { providersApi } from "@/lib/api";

export function remoteModelsQuery(providerId: string) {
  return queryOptions({
    queryKey: ["remoteModels", providerId],
    queryFn: () => providersApi.remoteModels(providerId).then((response) => response.data),
    staleTime: 60_000,
    retry: false,
    throwOnError: false,
  });
}
