import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import type { NavItem } from "@/stores/app-store";
import {
  LayoutDashboard,
  MessageCircle,
  Activity,
  Cpu,
  ListOrdered,
  Wrench,
  UserCircle,
  Brain,
  Plug,
  Radio,
  Settings,
  HeartPulse,
  ListChecks,
  PanelLeftClose,
  PanelLeft,
  Workflow,
  Tags,
  GraduationCap,
  SlidersHorizontal,
  Shield,
  Blocks,
  Smile,
  Database,
  type LucideIcon,
} from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  LayoutDashboard,
  MessageCircle,
  Activity,
  Cpu,
  ListOrdered,
  Wrench,
  UserCircle,
  Brain,
  Plug,
  Radio,
  Settings,
  HeartPulse,
  ListChecks,
  Workflow,
  Tags,
  GraduationCap,
  SlidersHorizontal,
  Shield,
  Blocks,
  Smile,
  Database,
};

const FALLBACK_NAV: NavItem[] = [
  { path: "/", label: "chat", icon: "MessageCircle", group: "group_core" },
  { path: "/dashboard", label: "dashboard", icon: "LayoutDashboard", group: "group_core" },
  { path: "/models", label: "models", icon: "Cpu", group: "group_core" },
  { path: "/personas", label: "personas", icon: "UserCircle", group: "group_core" },
  { path: "/memory", label: "memory", icon: "Brain", group: "group_core" },
  { path: "/data", label: "data", icon: "Database", group: "group_core" },
  { path: "/tasks", label: "tasks", icon: "ListChecks", group: "group_core" },
  { path: "/heartbeat", label: "heartbeat", icon: "HeartPulse", group: "group_core" },
  { path: "/tools", label: "tools", icon: "Wrench", group: "group_ability" },
  { path: "/skills", label: "skills", icon: "GraduationCap", group: "group_ability" },
  { path: "/mcp", label: "mcp", icon: "Plug", group: "group_ability" },
  { path: "/tags", label: "tags", icon: "Tags", group: "group_ability" },
  { path: "/channels", label: "channels", icon: "Radio", group: "group_ability" },
  { path: "/thinking", label: "thinking", icon: "Workflow", group: "group_ability" },
  { path: "/approvals", label: "approvals", icon: "Shield", group: "group_system" },
  { path: "/config", label: "config", icon: "SlidersHorizontal", group: "group_system" },
  { path: "/settings", label: "settings", icon: "Settings", group: "group_system" },
];

export function Sidebar() {
  const sidebarCollapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useAppStore((s) => s.toggleSidebar);
  const navigation = useAppStore((s) => s.navigation);
  const branding = useAppStore((s) => s.branding);
  const { t } = useTranslation("nav");

  const navItems = navigation.length > 0 ? navigation : FALLBACK_NAV;

  const groups = navItems.reduce<Record<string, NavItem[]>>((acc, item) => {
    const g = item.group || "other";
    (acc[g] ??= []).push(item);
    return acc;
  }, {});

  return (
    <aside
      className={cn(
        "flex flex-col h-full border-r border-border bg-panel transition-all duration-200",
        sidebarCollapsed ? "w-16" : "w-60",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-14 border-b border-border">
        {!sidebarCollapsed && (
          <span className="text-base font-semibold text-heading tracking-tight">
            {branding.title}
          </span>
        )}
        <button
          onClick={toggleSidebar}
          className="hidden md:block p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-colors"
        >
          {sidebarCollapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-2 px-2 overflow-y-auto">
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} className="mb-3">
            {!sidebarCollapsed && (
              <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-strong">
                {t(group, { defaultValue: group })}
              </div>
            )}
            <div className="space-y-0.5">
              {items.map((item) => {
                const Icon = ICON_MAP[item.icon] ?? LayoutDashboard;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === "/"}
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all duration-150",
                        isActive
                          ? "bg-accent-subtle text-accent border border-accent"
                          : "text-muted hover:text-foreground hover:bg-hover border border-transparent",
                        sidebarCollapsed && "justify-center px-0",
                      )
                    }
                  >
                    <Icon size={18} strokeWidth={1.5} />
                    {!sidebarCollapsed && <span>{t(item.label, { defaultValue: item.label })}</span>}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      {/* Version */}
      {!sidebarCollapsed && (
        <div className="px-4 py-3 border-t border-border text-xs text-muted">
          v{branding.version}
        </div>
      )}
    </aside>
  );
}
