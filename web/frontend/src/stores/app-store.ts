import { create } from "zustand";
import { readLocal, writeLocal } from "@/lib/storage";

export type Theme = "dark" | "light";
export type Density = "comfortable" | "compact";
export interface Branding { title: string; subtitle: string; version: string; }

interface AppState {
  theme: Theme;
  density: Density;
  favorites: string[];
  sidebarCollapsed: boolean;
  mobileMenuOpen: boolean;
  paletteOpen: boolean;
  branding: Branding;
  configLoaded: boolean;
  startedAt: number | null;
  toggleTheme: () => void;
  toggleDensity: () => void;
  toggleFavorite: (path: string) => void;
  toggleSidebar: () => void;
  setMobileMenuOpen: (open: boolean) => void;
  setPaletteOpen: (open: boolean) => void;
  setConfig: (config: { branding?: Branding }) => void;
  setStartedAt: (serverUptime: number) => void;
}

export function getInitialTheme(): Theme {
  const saved = readLocal("theme");
  return saved === "light" || saved === "dark" ? saved : window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readFavorites(): string[] {
  try {
    const value: unknown = JSON.parse(readLocal("anelf:favorites") ?? "[]");
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch { return []; }
}

export const useAppStore = create<AppState>((set) => ({
  theme: getInitialTheme(),
  density: readLocal("anelf:density") === "compact" ? "compact" : "comfortable",
  favorites: readFavorites(),
  sidebarCollapsed: readLocal("anelf:sidebar-collapsed") !== "false",
  mobileMenuOpen: false,
  paletteOpen: false,
  branding: { title: "AnelfAgent", subtitle: "Personal AI workspace", version: "0.3.0" },
  configLoaded: false,
  startedAt: null,
  toggleTheme: () => set((state) => {
    const theme = state.theme === "dark" ? "light" : "dark";
    writeLocal("theme", theme);
    document.documentElement.dataset.theme = theme;
    document.documentElement.classList.toggle("dark", theme === "dark");
    return { theme };
  }),
  toggleDensity: () => set((state) => {
    const density = state.density === "comfortable" ? "compact" : "comfortable";
    writeLocal("anelf:density", density);
    return { density };
  }),
  toggleFavorite: (path) => set((state) => {
    const favorites = state.favorites.includes(path) ? state.favorites.filter((value) => value !== path) : [...state.favorites, path];
    writeLocal("anelf:favorites", JSON.stringify(favorites));
    return { favorites };
  }),
  toggleSidebar: () => set((state) => {
    writeLocal("anelf:sidebar-collapsed", String(!state.sidebarCollapsed));
    return { sidebarCollapsed: !state.sidebarCollapsed };
  }),
  setMobileMenuOpen: (mobileMenuOpen) => set({ mobileMenuOpen }),
  setPaletteOpen: (paletteOpen) => set({ paletteOpen }),
  setConfig: (config) => set((state) => ({ branding: config.branding ?? state.branding, configLoaded: true })),
  setStartedAt: (uptime) => set((state) => {
    const startedAt = Date.now() / 1000 - uptime;
    return { startedAt: state.startedAt === null || Math.abs(startedAt - state.startedAt) > 5 ? startedAt : state.startedAt };
  }),
}));
