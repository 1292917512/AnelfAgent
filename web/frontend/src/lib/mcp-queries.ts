import { queryOptions } from "@tanstack/react-query";
import { mcpApi } from "./api/tools";

export const mcpServersQuery = () => queryOptions({
  queryKey: ["mcpServers"],
  queryFn: ({ signal }) => mcpApi.list(signal).then(({ data }) => data),
  throwOnError: false,
});
