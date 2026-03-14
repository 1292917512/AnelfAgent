import { create } from "zustand";

type Theme = "dark" | "light";

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

export interface ThemeConfig {
  primaryColor: string;
  accentColor: string;
  defaultTheme: Theme;
}

interface AppState {
  theme: Theme;
  sidebarCollapsed: boolean;
  branding: Branding;
  themeConfig: ThemeConfig;
  navigation: NavItem[];
  configLoaded: boolean;
  startedAt: number | null;

  toggleTheme: () => void;
  toggleSidebar: () => void;
  setConfig: (cfg: {
    branding?: Branding;
    theme?: ThemeConfig;
    navigation?: NavItem[];
  }) => void;
  setStartedAt: (serverUptime: number) => void;
}

const DEFAULT_BRANDING: Branding = {
  title: "AnelfAgent",
  subtitle: "Unified Agent Framework",
  version: "0.2.0",
};

const DEFAULT_THEME: ThemeConfig = {
  primaryColor: "#4a90d9",
  accentColor: "#14b8a6",
  defaultTheme: "dark",
};

export const useAppStore = create<AppState>((set, get) => ({
  theme: (localStorage.getItem("theme") as Theme) || "dark",
  sidebarCollapsed: false,
  branding: DEFAULT_BRANDING,
  themeConfig: DEFAULT_THEME,
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

  setConfig: (cfg) =>
    set((s) => ({
      branding: cfg.branding ?? s.branding,
      themeConfig: cfg.theme ?? s.themeConfig,
      navigation: cfg.navigation ?? s.navigation,
      configLoaded: true,
    })),

  setStartedAt: (serverUptime: number) => {
    if (get().startedAt !== null) return;
    set({ startedAt: Date.now() / 1000 - serverUptime });
  },
}));
