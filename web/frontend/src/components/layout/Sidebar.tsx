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
  SlidersHorizontal,
  PanelLeftClose,
  PanelLeft,
  Workflow,
  Tags,
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
  SlidersHorizontal,
  Workflow,
  Tags,
};

const FALLBACK_NAV: NavItem[] = [
  { path: "/", label: "chat", icon: "MessageCircle", group: "group_core" },
  { path: "/dashboard", label: "dashboard", icon: "LayoutDashboard", group: "group_core" },
  { path: "/status", label: "status", icon: "Activity", group: "group_agent" },
  { path: "/models", label: "models", icon: "Cpu", group: "group_agent" },
  { path: "/tags", label: "tags", icon: "Tags", group: "group_agent" },
  { path: "/tools", label: "tools", icon: "Wrench", group: "group_agent" },
  { path: "/personas", label: "personas", icon: "UserCircle", group: "group_agent" },
  { path: "/memory", label: "memory", icon: "Brain", group: "group_agent" },
  { path: "/mcp", label: "mcp", icon: "Plug", group: "group_agent" },
  { path: "/thinking", label: "thinking", icon: "Workflow", group: "group_agent" },
  { path: "/channels", label: "channels", icon: "Radio", group: "group_system" },
  { path: "/app-config", label: "app_config", icon: "SlidersHorizontal", group: "group_system" },
  { path: "/settings", label: "settings", icon: "Settings", group: "group_system" },
];

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar, navigation, branding } = useAppStore();
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
        "flex flex-col border-r border-[var(--border)] bg-[var(--panel)] transition-all duration-200",
        sidebarCollapsed ? "w-16" : "w-60",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-14 border-b border-[var(--border)]">
        {!sidebarCollapsed && (
          <span className="text-base font-semibold text-[var(--text-strong)] tracking-tight">
            {branding.title}
          </span>
        )}
        <button
          onClick={toggleSidebar}
          className="p-1.5 rounded-[var(--radius-md)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors"
        >
          {sidebarCollapsed ? <PanelLeft size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-2 px-2 overflow-y-auto">
        {Object.entries(groups).map(([group, items]) => (
          <div key={group} className="mb-3">
            {!sidebarCollapsed && (
              <div className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--muted-strong)]">
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
                        "flex items-center gap-3 px-3 py-2 rounded-[var(--radius-md)] text-sm font-medium transition-all duration-[var(--duration-fast)]",
                        isActive
                          ? "bg-[var(--accent-subtle)] text-[var(--accent)] border border-[var(--accent)]"
                          : "text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] border border-transparent",
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
        <div className="px-4 py-3 border-t border-[var(--border)] text-xs text-[var(--muted)]">
          v{branding.version}
        </div>
      )}
    </aside>
  );
}
