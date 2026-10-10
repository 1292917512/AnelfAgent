import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { contextApi } from "@/lib/api";

type SnapshotAction = "arm" | "disarm" | "clear" | boolean;
const snapshotKey = ["context", "snapshot"] as const;

export function useContextSnapshot() {
  const client = useQueryClient();
  const query = useQuery({
    queryKey: snapshotKey,
    queryFn: () => contextApi.snapshotGet().then((response) => response.data),
    throwOnError: false,
    refetchInterval: (state) => state.state.data?.status.armed ? 800 : state.state.data?.status.continuous ? 2000 : 10_000,
  });
  const action = useMutation({
    mutationFn: (value: SnapshotAction) => {
      if (typeof value === "boolean") return contextApi.snapshotSetContinuous(value);
      if (value === "arm") return contextApi.snapshotArm();
      if (value === "disarm") return contextApi.snapshotDisarm();
      return contextApi.snapshotClear();
    },
    onMutate: () => client.cancelQueries({ queryKey: snapshotKey }),
    onSettled: () => client.invalidateQueries({ queryKey: snapshotKey }),
  });
  return { ...query, action };
}
