import { create } from "zustand";
import { authApi } from "@/lib/api";
import i18n from "@/i18n";

interface AuthState {
  checked: boolean;
  required: boolean;
  authenticated: boolean;
  error: string;

  checkAuth: () => Promise<void>;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  checked: false,
  required: false,
  authenticated: false,
  error: "",

  checkAuth: async () => {
    try {
      const { data } = await authApi.check();
      set({
        checked: true,
        required: data.required,
        authenticated: data.authenticated,
      });
    } catch {
      set({ checked: true, required: false, authenticated: true });
    }
  },

  login: async (password: string) => {
    set({ error: "" });
    try {
      await authApi.login(password);
      set({ authenticated: true });
      return true;
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { error?: string } } })?.response?.data
          ?.error || i18n.t("loginFailed", { ns: "common" });
      set({ error: msg });
      return false;
    }
  },

  logout: async () => {
    try {
      await authApi.logout();
    } finally {
      set({ authenticated: false });
    }
  },
}));
