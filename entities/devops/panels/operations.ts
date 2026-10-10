import { create } from "zustand";
import { isAxiosError } from "axios";
import i18n from "@/i18n";
import { apiErrorMessage } from "@/lib/api";
import { devopsApi } from "./api";
import type { DevopsActionResult } from "./types";

export type DevopsAction = "restart" | "build" | "update" | "pull";
type Phase = "idle" | "pulling" | "building" | "restarting" | "ready" | "failed" | "updated";
const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const translate = (key: string) => i18n.t(key, { ns: "devops" });

function resultError(result: DevopsActionResult): string {
  const keys: Record<string, string> = { build_in_progress: "buildInProgress", dirty_workspace: "dirtyHint", pull_conflict: "conflictHint", frontend_not_found: "frontendNotFound" };
  const message = result.message || result.build?.log_tail || translate(keys[result.error ?? ""] ?? "operationFailed");
  return [message, result.detail].filter(Boolean).join("\n");
}

interface OperationState {
  phase: Phase;
  busy: boolean;
  detail: string;
  run: (action: DevopsAction) => Promise<void>;
}

/** 工作区、总览和实体面板共享的运维操作；切换页面不会重复提交或丢失进度。 */
export const useDevopsOperation = create<OperationState>((set, get) => ({
  phase: "idle", busy: false, detail: "",
  run: async (action) => {
    if (get().busy) return;
    set({ busy: true, detail: "", phase: action === "restart" ? "restarting" : action === "build" ? "building" : "pulling" });
    try {
      const trigger = { restart: devopsApi.restart, build: devopsApi.buildAndRestart, update: devopsApi.updateAndRestart, pull: devopsApi.update }[action];
      const { data } = await trigger();
      if (!data.ok) throw new Error(resultError(data));
      if (action === "pull") { set({ phase: "updated", detail: data.pull_result ?? "" }); return; }
      if (!data.runtime_id || (action !== "restart" && !data.operation_id)) throw new Error(translate("invalidOperation"));
      let deadline = Date.now() + (action === "restart" ? 240_000 : 600_000);
      while (Date.now() < deadline) {
        await sleep(2000);
        let state;
        try { state = (await devopsApi.buildState()).data; }
        catch (error) {
          if (isAxiosError(error) && error.response && error.response.status >= 400 && error.response.status < 500) throw error;
          continue;
        }
        if (state.runtime_id !== data.runtime_id) {
          if (!state.runtime_id) throw new Error(translate("invalidOperation"));
          set({ phase: "ready", detail: "" });
          return;
        }
        if (action !== "restart" && state.operation_id !== data.operation_id) throw new Error(translate("operationReplaced"));
        if (action !== "restart" && state.result?.ok === false) throw new Error(resultError(state.result));
        if (state.restarting) {
          if (get().phase !== "restarting") deadline = Date.now() + 240_000;
          set({ phase: "restarting" });
        } else if (state.building) set({ phase: state.phase === "pulling" ? "pulling" : "building" });
      }
      throw new Error(translate("operationTimeout"));
    } catch (error) {
      set({ phase: "failed", detail: apiErrorMessage(error, translate("operationFailed")) });
    } finally { set({ busy: false }); }
  },
}));
