/** 查询、SSE 恢复、设备命令反馈与配置刷新共用的数据入口。 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { apiErrorMessage } from "@/lib/api";
import { smartHomeApi } from "./api";
import type { CommandFeedback, DeviceDomainInfo, DevicesResponse, StateEventPayload } from "./types";

const QUERY_KEYS = ["smart-home-status", "smart-home-devices", "smart-home-domains", "smart-home-preview", "smart-home-entity-config"];

export function useSmartHomeData() {
  const { t } = useTranslation("smart_home");
  const queryClient = useQueryClient();
  const inFlight = useRef(new Set<string>());
  const [pendingIds, setPendingIds] = useState<Set<string>>(new Set());
  const [feedback, setFeedback] = useState<Record<string, CommandFeedback>>({});
  const [streamConnected, setStreamConnected] = useState(false);
  const statusQuery = useQuery({ queryKey: [QUERY_KEYS[0]], queryFn: () => smartHomeApi.status().then((r) => r.data), refetchInterval: 30000 });
  const devicesQuery = useQuery({ queryKey: [QUERY_KEYS[1]], queryFn: () => smartHomeApi.devices().then((r) => r.data), refetchInterval: 30000 });
  const domainsQuery = useQuery({ queryKey: [QUERY_KEYS[2]], queryFn: () => smartHomeApi.domains().then((r) => r.data) });
  const previewQuery = useQuery({ queryKey: [QUERY_KEYS[3]], queryFn: () => smartHomeApi.preview().then((r) => r.data), refetchInterval: 30000 });

  const refreshAll = useCallback(() => {
    for (const key of QUERY_KEYS) void queryClient.invalidateQueries({ queryKey: [key] });
  }, [queryClient]);

  useEffect(() => {
    const source = new EventSource("/api/entity/smart_home/stream");
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (timer !== undefined) return;
      timer = setTimeout(() => { timer = undefined; refreshAll(); }, 1000);
    };
    source.onopen = () => { setStreamConnected(true); refresh(); };
    source.onerror = () => setStreamConnected(false);
    source.addEventListener("state", (raw) => {
      try {
        const payload = JSON.parse((raw as MessageEvent<string>).data) as StateEventPayload;
        const device = payload.device;
        if (!device?.entity_id) return;
        queryClient.setQueryData<DevicesResponse>(["smart-home-devices"], (old) => {
          if (!old) { refresh(); return old; }
          const exists = old.devices.some((item) => item.entity_id === device.entity_id);
          const devices = exists ? old.devices.map((item) => item.entity_id === device.entity_id ? device : item) : [...old.devices, device];
          return { ...old, devices, count: devices.length };
        });
      } catch { refresh(); }
    });
    source.addEventListener("sync", refresh);
    source.addEventListener("connection", refresh);
    return () => { source.close(); if (timer !== undefined) clearTimeout(timer); };
  }, [queryClient, refreshAll]);

  const devices = useMemo(() => devicesQuery.data?.devices ?? [], [devicesQuery.data]);
  const domains = useMemo(() => domainsQuery.data?.domains ?? [], [domainsQuery.data]);
  const domainByHa = useMemo(() => {
    const map = new Map<string, DeviceDomainInfo>();
    for (const domain of domains) for (const key of domain.ha_domains) if (!map.has(key)) map.set(key, domain);
    return map;
  }, [domains]);

  const saveConfig = async (key: string, value: unknown) => {
    await smartHomeApi.updateConfig(key, value);
    await Promise.all(QUERY_KEYS.map((queryKey) => queryClient.invalidateQueries({ queryKey: [queryKey] })));
  };
  const control = async (entityId: string, action: string, value = ""): Promise<boolean> => {
    if (inFlight.current.has(entityId)) return false;
    inFlight.current.add(entityId);
    setPendingIds(new Set(inFlight.current));
    try {
      await smartHomeApi.control(entityId, action, value);
      setFeedback((old) => ({ ...old, [entityId]: { kind: "accepted", message: t("messages.accepted"), at: Date.now() } }));
      refreshAll();
      return true;
    } catch (error) {
      setFeedback((old) => ({ ...old, [entityId]: { kind: "error", message: apiErrorMessage(error, t("messages.controlFailed")), at: Date.now() } }));
      return false;
    } finally {
      inFlight.current.delete(entityId);
      setPendingIds(new Set(inFlight.current));
    }
  };
  return {
    status: statusQuery.data, devices, domains, domainByHa, pendingIds, feedback, streamConnected,
    preview: previewQuery.data, refreshAll, saveConfig, control,
    refreshPreview: () => void previewQuery.refetch(),
    isError: (!devicesQuery.data && devicesQuery.isError) || (!statusQuery.data && statusQuery.isError) || (!domainsQuery.data && domainsQuery.isError),
    hasRefreshError: devicesQuery.isError || statusQuery.isError || domainsQuery.isError,
    isLoading: statusQuery.isPending || devicesQuery.isPending || domainsQuery.isPending,
    refreshing: statusQuery.isFetching || devicesQuery.isFetching,
  };
}

export type SmartHomeData = ReturnType<typeof useSmartHomeData>;
