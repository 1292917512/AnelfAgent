import { create } from "zustand";
import { authApi, apiErrorMessage } from "@/lib/api";
import i18n from "@/i18n";

interface AuthState {
  checked: boolean;
  required: boolean;
  authenticated: boolean;
  unavailable: boolean;
  error: string;
  checkAuth: () => Promise<void>;
  login: (password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  expireSession: () => void;
}

let pendingCheck: Promise<void> | null = null;

export const useAuthStore = create<AuthState>((set) => ({
  checked: false, required: true, authenticated: false, unavailable: false, error: "",
  checkAuth: () => {
    if (pendingCheck) return pendingCheck;
    set({ checked: false, error: "", unavailable: false });
    pendingCheck = authApi.check().then(({ data }) => {
      set({ checked: true, required: data.required, authenticated: data.authenticated, unavailable: false });
    }).catch((error: unknown) => {
      set({ checked: true, required: true, authenticated: false, unavailable: true, error: apiErrorMessage(error, i18n.t("requestFailed")) });
    }).finally(() => { pendingCheck = null; });
    return pendingCheck;
  },
  login: async (password) => {
    set({ error: "" });
    try {
      await authApi.login(password);
      set({ authenticated: true, unavailable: false });
      return true;
    } catch (error: unknown) {
      set({ error: apiErrorMessage(error, i18n.t("loginFailed")) });
      return false;
    }
  },
  logout: async () => {
    await authApi.logout();
    set({ authenticated: false, required: true });
  },
  expireSession: () => set({ checked: true, required: true, authenticated: false, unavailable: false, error: i18n.t("sessionExpired") }),
}));
