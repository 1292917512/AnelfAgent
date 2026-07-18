import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { MessageCircle, LayoutDashboard, Brain, GraduationCap, Settings } from "lucide-react";
import { cn } from "@/lib/utils";

/** 移动端核心导航项（精简版，覆盖高频页面） */
const MOBILE_TABS = [
  { path: "/", label: "chat", icon: MessageCircle },
  { path: "/dashboard", label: "dashboard", icon: LayoutDashboard },
  { path: "/memory", label: "memory", icon: Brain },
  { path: "/skills", label: "skills", icon: GraduationCap },
  { path: "/settings", label: "settings", icon: Settings },
];

/** 移动端底部 TabBar（< 768px 显示） */
export function MobileNav() {
  const { t } = useTranslation("nav");

  return (
    <nav className="flex md:hidden items-stretch border-t border-[var(--border)] bg-[var(--panel)] safe-area-bottom">
      {MOBILE_TABS.map((tab) => {
        const Icon = tab.icon;
        return (
          <NavLink
            key={tab.path}
            to={tab.path}
            end={tab.path === "/"}
            className={({ isActive }) =>
              cn(
                "flex-1 flex flex-col items-center justify-center gap-0.5 py-2 min-h-[52px] text-[10px] font-medium transition-colors",
                isActive ? "text-[var(--accent)]" : "text-[var(--muted)]",
              )
            }
          >
            <Icon size={20} strokeWidth={1.8} />
            <span>{t(tab.label, { defaultValue: tab.label })}</span>
          </NavLink>
        );
      })}
    </nav>
  );
}
