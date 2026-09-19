/**
 * 侧边栏导航单一事实源 —— 代码常量，不走配置。
 *
 * 路由与页面在 `core-routes.ts` 注册，新增核心页面时同步在此追加一行。
 * 图标名对应 `Sidebar.tsx` 的 `ICON_MAP`。
 * 分组 key 对应前端 i18n 的 nav 命名空间（group_core / group_ability / group_system）。
 */

export interface NavItem {
  path: string;
  label: string;
  icon: string;
  group: string;
}

export const NAVIGATION: NavItem[] = [
  { path: "/", label: "chat", icon: "MessageCircle", group: "group_core" },
  { path: "/dashboard", label: "dashboard", icon: "LayoutDashboard", group: "group_core" },
  { path: "/models", label: "models", icon: "Cpu", group: "group_core" },
  { path: "/personas", label: "personas", icon: "UserCircle", group: "group_core" },
  { path: "/memory", label: "memory", icon: "Brain", group: "group_core" },
  { path: "/tasks", label: "tasks", icon: "ListChecks", group: "group_core" },
  { path: "/heartbeat", label: "heartbeat", icon: "HeartPulse", group: "group_core" },
  { path: "/vision", label: "vision", icon: "Eye", group: "group_core" },
  { path: "/sound", label: "sound", icon: "AudioLines", group: "group_core" },
  { path: "/retrieval", label: "retrieval", icon: "Search", group: "group_core" },
  { path: "/tools", label: "tools", icon: "Wrench", group: "group_ability" },
  { path: "/skills", label: "skills", icon: "GraduationCap", group: "group_ability" },
  { path: "/mcp", label: "mcp", icon: "Plug", group: "group_ability" },
  { path: "/tags", label: "tags", icon: "Tags", group: "group_ability" },
  { path: "/channels", label: "channels", icon: "Radio", group: "group_ability" },
  { path: "/thinking", label: "thinking", icon: "Workflow", group: "group_ability" },
  { path: "/data", label: "data", icon: "Database", group: "group_system" },
  { path: "/approvals", label: "approvals", icon: "Shield", group: "group_system" },
  { path: "/config", label: "config", icon: "SlidersHorizontal", group: "group_system" },
  { path: "/settings", label: "settings", icon: "Settings", group: "group_system" },
];
