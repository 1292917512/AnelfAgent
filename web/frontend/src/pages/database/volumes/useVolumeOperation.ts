import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { volumeApi } from "@/lib/api";

/** 完成通知以任务时间戳去重，刷新概览与打开的备份清单；快速任务同样生效。 */
export function useVolumeOperation(volumeId: string) {
  const client = useQueryClient();
  const seen = useRef("");
  const query = useQuery({
    queryKey: ["volumeOp", volumeId],
    queryFn: () => volumeApi.operation(volumeId).then((r) => r.data),
    refetchInterval: (q) => q.state.data?.state === "running" ? 1000 : 30000,
  });
  const op = query.data;
  useEffect(() => {
    if (!op || !["done", "error", "cancelled"].includes(op.state)) return;
    const key = `${volumeId}:${op.op}:${op.started_at}:${op.finished_at}:${op.state}`;
    if (seen.current === key) return;
    seen.current = key;
    void client.invalidateQueries({ queryKey: ["dbVolumes"] });
    void client.invalidateQueries({ queryKey: ["volumeBackups", volumeId] });
  }, [client, volumeId, op]);
  return query;
}
