/** 智能家居面板数据钩子 — 查询/SSE 实时推送/控制调用/配置保存的唯一收口。
 *
 * state 事件就地更新设备缓存，sync/connection 事件整表刷新；
 * 控制调用维护 pending 集合供卡片展示进行态。
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "@/stores/toast-store";
import { useTranslation } from "react-i18next";
import { smartHomeApi } from "./api";
import type {
  DeviceDomainInfo,
  DevicesResponse,
  StateEventPayload,
  StatusResponse,
} from "./types";

export function useSmartHomeData() {
  const { t } = useTranslation("smart_home");
  const queryClient = useQueryClient();
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());

  const statusQuery = useQuery({
    queryKey: ["smart-home-status"],
    queryFn: () => smartHomeApi.status().then((r) => r.data),
    refetchInterval: 30000,
  });
  const devicesQuery = useQuery({
    queryKey: ["smart-home-devices"],
    queryFn: () => smartHomeApi.devices().then((r) => r.data),
    refetchInterval: 60000,
  });
  const domainsQuery = useQuery({
    queryKey: ["smart-home-domains"],
    queryFn: () => smartHomeApi.domains().then((r) => r.data),
  });
  const previewQuery = useQuery({
    queryKey: ["smart-home-preview"],
    queryFn: () => smartHomeApi.preview().then((r) => r.data),
    refetchInterval: 30000,
  });

  const refreshAll = () => {
    void queryClient.invalidateQueries({ queryKey: ["smart-home-status"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-devices"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-domains"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-preview"] });
    void queryClient.invalidateQueries({ queryKey: ["smart-home-entity-config"] });
  };

  useEffect(() => {
    const es = new EventSource("/api/entity/smart_home/stream");
    es.addEventListener("state", (raw) => {
      try {
        const payload = JSON.parse((raw as MessageEvent).data) as StateEventPayload;
        if (!payload.device) return;
        queryClient.setQueryData<DevicesResponse>(["smart-home-devices"], (old) => {
          if (!old) return old;
          const idx = old.devices.findIndex(
            (d) => d.entity_id === payload.device!.entity_id,
          );
          if (idx < 0) return old;
          const devices = [...old.devices];
          devices[idx] = payload.device!;
          return { ...old, devices };
        });
      } catch {
        /* 忽略畸形事件 */
      }
    });
    es.addEventListener("sync", refreshAll);
    es.addEventListener("connection", refreshAll);
    return () => es.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);

  const status: StatusResponse | undefined = statusQuery.data;
  const devices = useMemo(
    () => devicesQuery.data?.devices ?? [],
    [devicesQuery.data],
  );
  const domains = useMemo(
    () => domainsQuery.data?.domains ?? [],
    [domainsQuery.data],
  );

  /** ha domain → 设备域组件（面板按域特化渲染控制件） */
  const domainByHa = useMemo(() => {
    const map = new Map<string, DeviceDomainInfo>();
    for (const domain of domains) {
      for (const ha of domain.ha_domains) {
        if (!map.has(ha)) map.set(ha, domain);
      }
    }
    return map;
  }, [domains]);

  const saveConfig = async (key: string, value: unknown) => {
    await smartHomeApi.updateConfig(key, value);
    await domainsQuery.refetch();
    await previewQuery.refetch();
  };

  const control = (entityId: string, action: string, value = "") => {
    setPendingIds((prev) => new Set(prev).add(entityId));
    smartHomeApi
      .control(entityId, action, value)
      .catch((err: { response?: { data?: { detail?: string } } }) => {
        toast.error(err.response?.data?.detail || t("messages.controlFailed"));
      })
      .finally(() => {
        setPendingIds((prev) => {
          const next = new Set(prev);
          next.delete(entityId);
          return next;
        });
      });
  };

  return {
    status,
    devices,
    domains,
    preview: previewQuery.data,
    domainByHa,
    pendingIds,
    refreshAll,
    refreshPreview: () => void previewQuery.refetch(),
    saveConfig,
    control,
    isError: devicesQuery.isError || statusQuery.isError,
    isLoading: devicesQuery.isLoading || !status,
  };
}

export type SmartHomeData = ReturnType<typeof useSmartHomeData>;
