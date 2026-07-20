import { create } from "zustand";

export type Theme = "dark" | "light";

export interface NavItem {
  path: string;
  label: string;
  icon: string;
  group: string;
}

export interface Branding {
  title: string;
  subtitle: string;
  version: string;
}

interface AppState {
  theme: Theme;
  sidebarCollapsed: boolean;
  mobileMenuOpen: boolean;
  branding: Branding;
  navigation: NavItem[];
  configLoaded: boolean;
  startedAt: number | null;

  toggleTheme: () => void;
  toggleSidebar: () => void;
  setMobileMenuOpen: (open: boolean) => void;
  setConfig: (cfg: { branding?: Branding; navigation?: NavItem[] }) => void;
  setStartedAt: (serverUptime: number) => void;
}

const DEFAULT_BRANDING: Branding = {
  title: "AnelfAgent",
  subtitle: "Unified Agent Framework",
  version: "0.2.0",
};

/** 初始主题：本地存储 > 系统偏好 > 默认暗色 */
export function getInitialTheme(): Theme {
  const saved = localStorage.getItem("theme");
  if (saved === "dark" || saved === "light") return saved;
  if (window.matchMedia?.("(prefers-color-scheme: light)").matches) return "light";
  return "dark";
}

export const useAppStore = create<AppState>((set, get) => ({
  theme: getInitialTheme(),
  sidebarCollapsed: false,
  mobileMenuOpen: false,
  branding: DEFAULT_BRANDING,
  navigation: [],
  configLoaded: false,
  startedAt: null,

  toggleTheme: () =>
    set((s) => {
      const next = s.theme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", next);
      document.documentElement.setAttribute("data-theme", next);
      return { theme: next };
    }),

  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  setMobileMenuOpen: (open: boolean) => set({ mobileMenuOpen: open }),

  setConfig: (cfg) =>
    set((s) => ({
      branding: cfg.branding ?? s.branding,
      navigation: cfg.navigation ?? s.navigation,
      configLoaded: true,
    })),

  setStartedAt: (serverUptime: number) => {
    if (get().startedAt !== null) return;
    set({ startedAt: Date.now() / 1000 - serverUptime });
  },
}));
