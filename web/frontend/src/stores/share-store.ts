import { create } from "zustand";
import type { ShareLink, ShareStats } from "@/lib/types";

interface ShareState {
  // 列表状态
  links: ShareLink[];
  total: number;
  page: number;
  pageSize: number;
  status: string;
  query: string;
  loading: boolean;

  // 统计状态
  stats: ShareStats | null;

  // 创建状态
  creating: boolean;
  lastCreated: ShareLink | null;

  // Actions
  setLinks: (links: ShareLink[], total: number) => void;
  setPage: (page: number) => void;
  setStatus: (status: string) => void;
  setQuery: (query: string) => void;
  setLoading: (loading: boolean) => void;
  setStats: (stats: ShareStats) => void;
  setCreating: (creating: boolean) => void;
  setLastCreated: (link: ShareLink | null) => void;
  reset: () => void;
}

export const useShareStore = create<ShareState>((set) => ({
  links: [],
  total: 0,
  page: 1,
  pageSize: 20,
  status: "active",
  query: "",
  loading: false,

  stats: null,

  creating: false,
  lastCreated: null,

  setLinks: (links, total) => set({ links, total }),
  setPage: (page) => set({ page }),
  setStatus: (status) => set({ status, page: 1 }),
  setQuery: (query) => set({ query, page: 1 }),
  setLoading: (loading) => set({ loading }),
  setStats: (stats) => set({ stats }),
  setCreating: (creating) => set({ creating }),
  setLastCreated: (lastCreated) => set({ lastCreated }),
  reset: () =>
    set({
      links: [],
      total: 0,
      page: 1,
      status: "active",
      query: "",
      loading: false,
      stats: null,
      creating: false,
      lastCreated: null,
    }),
}));
