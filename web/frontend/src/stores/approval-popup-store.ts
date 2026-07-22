/**
 * 审批弹窗状态 — SSE approval_request 事件驱动。
 *
 * webui 频道把审批请求以 SSE 推送（不再走纯文本消息），
 * 弹窗全局挂载，决策走 /approvals/:id/approve|deny REST。
 */
import { create } from "zustand";

export interface ApprovalRequestPayload {
  request_id: string;
  tool_name: string;
  tool_args: string;
  risk_level: string;
  reason: string;
  timeout_seconds: number;
  received_at: number;
}

interface ApprovalPopupState {
  /** 待处理队列（先进先出，一次只弹一个） */
  queue: ApprovalRequestPayload[];
  push: (payload: ApprovalRequestPayload) => void;
  dismiss: (requestId: string) => void;
}

export const useApprovalPopupStore = create<ApprovalPopupState>((set) => ({
  queue: [],
  push: (payload) =>
    set((s) =>
      s.queue.some((q) => q.request_id === payload.request_id)
        ? s
        : { queue: [...s.queue, payload] },
    ),
  dismiss: (requestId) =>
    set((s) => ({ queue: s.queue.filter((q) => q.request_id !== requestId) })),
}));
