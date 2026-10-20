import { queryOptions } from "@tanstack/react-query";
import { statusApi } from "./api/status";

/** 复用总览和顶栏的查询身份；运行态只作观测，不驱动智能体调度。 */
export const runtimeStatusQuery = () => queryOptions({
  queryKey: ["status"],
  queryFn: ({ signal }) => statusApi.get(signal).then((response) => response.data),
  retry: false, throwOnError: false,
});

export const runtimeThinkingQuery = () => queryOptions({
  queryKey: ["thinking"],
  queryFn: ({ signal }) => statusApi.thinking(signal).then((response) => response.data),
  retry: false, throwOnError: false,
});
