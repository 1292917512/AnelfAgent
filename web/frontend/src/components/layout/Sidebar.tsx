import { NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bot, PanelLeftClose, PanelLeftOpen, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { getNavigation, NAVIGATION_GROUPS, type NavItem } from "@/lib/navigation";
import { Button } from "@/components/ui/Button";

function NavigationLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { t } = useTranslation("nav");
  const favorite = useAppStore((state) => state.favorites.includes(item.path));
  const toggleFavorite = useAppStore((state) => state.toggleFavorite);
  const label = t(item.label, { defaultValue: item.label });
  return (
    <div className="sidebar-item">
      <NavLink to={item.path} end={item.path === "/"} title={collapsed ? label : undefined}
        className={({ isActive }) => cn("sidebar-link", isActive && "is-active")}
        aria-label={label}>
        <item.icon size={18} strokeWidth={1.7} className="sidebar-icon" />
        {!collapsed && <span className="truncate">{label}</span>}
      </NavLink>
      {!collapsed && <button type="button" data-favorite-path={item.path}
        onClick={(event) => {
          const sidebar = event.currentTarget.closest("aside");
          toggleFavorite(item.path);
          requestAnimationFrame(() => sidebar?.querySelector<HTMLButtonElement>(
            `button[data-favorite-path="${CSS.escape(item.path)}"]`,
          )?.focus({ preventScroll: true }));
        }}
        aria-label={t(favorite ? "unpin" : "pin", { name: label })}
        title={t(favorite ? "unpin" : "pin", { name: label })}
        aria-pressed={favorite} className={cn("sidebar-pin", favorite && "is-pinned")}>
        <Star size={13} fill={favorite ? "currentColor" : "none"} />
      </button>}
    </div>
  );
}

export function Sidebar({ mobile = false }: { mobile?: boolean }) {
  const { t } = useTranslation("nav");
  const collapsed = useAppStore((state) => !mobile && state.sidebarCollapsed);
  const location = useLocation();
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const branding = useAppStore((state) => state.branding);
  const favorites = useAppStore((state) => state.favorites);
  const items = getNavigation().filter((item) => !collapsed
    || item.mobile || favorites.includes(item.path) || item.path === location.pathname
    || ["/thinking", "/context", "/models", "/config"].includes(item.path));
  const favoriteItems = collapsed ? [] : items.filter((item) => favorites.includes(item.path));
  return (
    <aside data-collapsed={collapsed} data-mobile={mobile}
      className={cn("app-sidebar flex h-full shrink-0 flex-col", mobile ? "w-full" : collapsed ? "w-[68px]" : "w-[236px]")}>
      <div className="sidebar-brand">
        {collapsed ? <Button variant="ghost" size="icon" className="sidebar-expand"
          onClick={toggleSidebar} aria-expanded={false} aria-label={t("expandSidebar")} title={t("expandSidebar")}>
          <Bot size={23} strokeWidth={1.7} className="sidebar-brand-symbol" />
          <PanelLeftOpen size={20} className="sidebar-expand-symbol" />
        </Button> : <>
          <div className="brand-mark"><Bot size={22} strokeWidth={1.7} /></div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-semibold tracking-tight text-heading">{branding.title}</div>
            <div className="truncate text-[10px] text-muted">{t("personalWorkspace")}</div>
          </div>
          {!mobile && <Button variant="ghost" size="icon" className="shrink-0"
            onClick={toggleSidebar} aria-expanded aria-label={t("collapseSidebar")} title={t("collapseSidebar")}>
            <PanelLeftClose size={16} />
          </Button>}
        </>}
      </div>
      <nav aria-label={t("navigation")} className="sidebar-navigation min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {favoriteItems.length > 0 && <section aria-label={t("favorites")}>
          <h2>{t("favorites")}</h2>
          <div className="navigation-group">{favoriteItems.map((item) => <NavigationLink key={item.path} item={item} collapsed={false} />)}</div>
        </section>}
        {NAVIGATION_GROUPS.map((group) => {
          const groupItems = items.filter((item) => item.group === group && (collapsed || !favorites.includes(item.path)));
          return groupItems.length > 0 && <section key={group} aria-label={t(`groups.${group}`)}>
            <h2>{t(`groups.${group}`)}</h2>
            <div className="navigation-group">{groupItems.map((item) => <NavigationLink key={item.path} item={item} collapsed={collapsed} />)}</div>
          </section>;
        })}
      </nav>
      <div className="sidebar-footer">
        {!collapsed && <span className="truncate">{branding.title}</span>}
        <span className="font-mono tabular-nums">v{branding.version}</span>
      </div>
    </aside>
  );
}
