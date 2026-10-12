import { api } from "./client";
import type { NetworkProbeReport, NetworkProbeRequest } from "@/lib/types/network";

export const networkApi = {
  diagnose: (data: NetworkProbeRequest) => api.post<NetworkProbeReport>("/network/diagnose", data, {
    timeout: (data.samples * data.timeout_seconds + 5) * 1000,
  }),
};
