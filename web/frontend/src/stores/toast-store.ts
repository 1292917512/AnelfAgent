import { create } from "zustand";

export type ToastType = "success" | "error" | "info";

export interface ToastItem {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastState {
  toasts: ToastItem[];
  push: (type: ToastType, message: string, duration?: number) => void;
  dismiss: (id: number) => void;
}

let nextId = 1;

/** 全局通知状态：最多保留 5 条，到期自动消失 */
export const useToastStore = create<ToastState>((set) => ({
  toasts: [],

  push: (type, message, duration = 4000) => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts.slice(-4), { id, type, message }] }));
    if (duration > 0) {
      setTimeout(() => useToastStore.getState().dismiss(id), duration);
    }
  },

  dismiss: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));

/** 全局通知入口：toast.success / toast.error / toast.info */
export const toast = {
  success: (message: string, duration?: number) =>
    useToastStore.getState().push("success", message, duration),
  error: (message: string, duration?: number) =>
    useToastStore.getState().push("error", message, duration ?? 6000),
  info: (message: string, duration?: number) =>
    useToastStore.getState().push("info", message, duration),
};
