/**
 * 核心路由注册表 — 声明式集中管理，新增核心页面只需在此追加一行。
 * - index: true → 首页（path="/"）
 * - redirectTo → Navigate 重定向（无需 page）
 * - path 含 ":" → 参数路由（如 entities/:name）
 *
 * 插件整页路由（channels/<id>/frontend 的 route 声明）不在此表，
 * 由 channel-plugins 注册表动态追加（见 App.tsx）。
 */
export interface CoreRoute {
  path?: string;
  page?: string;
  index?: boolean;
  redirectTo?: string;
}

export const CORE_ROUTES: CoreRoute[] = [
  { index: true, page: "Chat" },
  { path: "dashboard", page: "Dashboard" },
  { path: "status", page: "Chat", redirectTo: "/" },
  { path: "models", page: "Models" },
  { path: "capabilities", page: "Tools", redirectTo: "/tools" },
  { path: "tools", page: "Tools" },
  { path: "entities/:name", page: "EntityDetail" },
  { path: "skills", page: "Skills" },
  { path: "mcp", page: "Mcp" },
  { path: "tags", page: "Tags" },
  { path: "personas", page: "Personas" },
  { path: "memory", page: "Memory" },
  { path: "stickers", page: "Stickers" },
  { path: "data", page: "Data" },
  { path: "config", page: "Config" },
  { path: "channels", page: "Channels" },
  { path: "approvals", page: "Approvals" },
  { path: "tasks", page: "Tasks" },
  { path: "heartbeat", page: "Heartbeat" },
  { path: "thinking", page: "Thinking" },
  { path: "settings", page: "Settings" },
];

/** 核心路由 path 集合（"/x" 形式；Sidebar 据此识别插件导航项） */
export const CORE_ROUTE_PATHS = new Set(
  CORE_ROUTES.flatMap((r) => {
    const paths: string[] = [];
    if (r.path) paths.push(`/${r.path}`);
    if (r.index || r.redirectTo === "/") paths.push("/");
    if (r.redirectTo) paths.push(r.redirectTo);
    return paths;
  }),
);
