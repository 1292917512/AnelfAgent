import axios from "axios";
import { QueryClient } from "@tanstack/react-query";

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: false,
        retry: (count, error) => count < 1 && !(axios.isAxiosError(error) && error.response && error.response.status < 500),
        throwOnError: (_error, query) => query.state.data === undefined,
      },
      mutations: { retry: false },
    },
  });
}
