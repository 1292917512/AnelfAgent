import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import { AudioLines, Brain, Cpu, Database, Eye, GraduationCap, HeartPulse, LayoutDashboard, ListChecks, MessageCircle, Plug, Radio, ScanText, Search, Settings, Shield, SlidersHorizontal, Tags, UserCircle, Waypoints, Webhook, Workflow, Wrench, type LucideIcon } from "lucide-react";

export type NavigationGroup = "workspace" | "agent" | "capabilities" | "system";

export interface CoreRoute {
  path: string;
  label: string;
  description?: string;
  icon: LucideIcon;
  group: NavigationGroup;
  page: LazyExoticComponent<ComponentType>;
  navigation?: boolean;
  mobile?: boolean;
  workspace?: boolean;
}

export const CORE_ROUTES: CoreRoute[] = [
  { path: "/", label: "chat", icon: MessageCircle, group: "workspace", page: lazy(() => import("@/pages/Chat")), mobile: true, workspace: true },
  { path: "/dashboard", label: "dashboard", icon: LayoutDashboard, group: "workspace", page: lazy(() => import("@/pages/Dashboard")), mobile: true },
  { path: "/tasks", label: "tasks", icon: ListChecks, group: "workspace", page: lazy(() => import("@/pages/Tasks")), mobile: true },
  { path: "/workflow", label: "workflow", icon: Waypoints, group: "workspace", page: lazy(() => import("@/pages/Workflow")) },
  { path: "/models", label: "models", icon: Cpu, group: "agent", page: lazy(() => import("@/pages/Models")) },
  { path: "/personas", label: "personas", icon: UserCircle, group: "agent", page: lazy(() => import("@/pages/Personas")) },
  { path: "/memory", label: "memory", icon: Brain, group: "agent", page: lazy(() => import("@/pages/Memory")), mobile: true },
  { path: "/skills", label: "skills", icon: GraduationCap, group: "agent", page: lazy(() => import("@/pages/Skills")) },
  { path: "/heartbeat", label: "heartbeat", icon: HeartPulse, group: "agent", page: lazy(() => import("@/pages/Heartbeat")) },
  { path: "/thinking", label: "thinking", icon: Workflow, group: "agent", page: lazy(() => import("@/pages/Thinking")), workspace: true },
  { path: "/context", label: "context", icon: ScanText, group: "agent", page: lazy(() => import("@/pages/Context")), workspace: true },
  { path: "/tools", label: "tools", icon: Wrench, group: "capabilities", page: lazy(() => import("@/pages/Tools")) },
  { path: "/mcp", label: "mcp", icon: Plug, group: "capabilities", page: lazy(() => import("@/pages/Mcp")) },
  { path: "/channels", label: "channels", icon: Radio, group: "capabilities", page: lazy(() => import("@/pages/Channels")) },
  { path: "/vision", label: "vision", icon: Eye, group: "capabilities", page: lazy(() => import("@/pages/Vision")) },
  { path: "/sound", label: "sound", icon: AudioLines, group: "capabilities", page: lazy(() => import("@/pages/Sound")) },
  { path: "/retrieval", label: "retrieval", icon: Search, group: "capabilities", page: lazy(() => import("@/pages/Retrieval")) },
  { path: "/tags", label: "tags", icon: Tags, group: "capabilities", page: lazy(() => import("@/pages/Tags")) },
  { path: "/approvals", label: "approvals", icon: Shield, group: "system", page: lazy(() => import("@/pages/Approvals")) },
  { path: "/data", label: "data", icon: Database, group: "system", page: lazy(() => import("@/pages/Data")) },
  { path: "/hooks", label: "hooks", icon: Webhook, group: "system", page: lazy(() => import("@/pages/Hooks")) },
  { path: "/config", label: "config", icon: SlidersHorizontal, group: "system", page: lazy(() => import("@/pages/Config")) },
  { path: "/settings", label: "settings", icon: Settings, group: "system", page: lazy(() => import("@/pages/Settings")) },
  { path: "/entities/:name", label: "entity", icon: Wrench, group: "capabilities", page: lazy(() => import("@/pages/EntityDetail")), navigation: false },
];
