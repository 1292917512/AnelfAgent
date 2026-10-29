import { queryOptions } from "@tanstack/react-query";
import { modelsApi } from "./api/models";

/** 模型选择与配置引导共用同一查询，模型配置变更统一使 priorities 失效。 */
export const modelPrioritiesQuery = () => queryOptions({
  queryKey: ["priorities"],
  queryFn: ({ signal }) => modelsApi.priorities(signal).then((response) => response.data),
  throwOnError: false,
});
