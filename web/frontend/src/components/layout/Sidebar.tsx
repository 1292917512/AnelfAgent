import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Bot, PanelLeftClose, PanelLeftOpen, Search, Star } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";
import { getNavigation, NAVIGATION_GROUPS, type NavItem } from "@/lib/navigation";
import { Button } from "@/components/ui/Button";

function NavigationLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { t } = useTranslation("nav");
  const favorites = useAppStore((state) => state.favorites);
  const toggleFavorite = useAppStore((state) => state.toggleFavorite);
  const favorite = favorites.includes(item.path);
  return (
    <div className="group/nav relative">
      <NavLink to={item.path} end={item.path === "/"} title={collapsed ? t(item.label, { defaultValue: item.label }) : undefined}
        className={({ isActive }) => cn("sidebar-link", isActive && "is-active", collapsed && "justify-center !px-0")}
        aria-label={t(item.label, { defaultValue: item.label })}>
        <item.icon size={18} strokeWidth={1.7} className="shrink-0" />
        {!collapsed && <span className="truncate pr-5">{t(item.label, { defaultValue: item.label })}</span>}
      </NavLink>
      {!collapsed && <button type="button" onClick={() => toggleFavorite(item.path)}
        aria-label={t(favorite ? "unpin" : "pin", { name: t(item.label, { defaultValue: item.label }) })}
        aria-pressed={favorite}
        className={cn("absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-muted opacity-0 transition-opacity hover:text-accent group-hover/nav:opacity-100 focus-visible:opacity-100", favorite && "opacity-100 text-accent")}>
        <Star size={12} fill={favorite ? "currentColor" : "none"} />
      </button>}
    </div>
  );
}

export function Sidebar({ mobile = false }: { mobile?: boolean }) {
  const { t } = useTranslation("nav");
  const collapsed = useAppStore((state) => !mobile && state.sidebarCollapsed);
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const setPaletteOpen = useAppStore((state) => state.setPaletteOpen);
  const branding = useAppStore((state) => state.branding);
  const favorites = useAppStore((state) => state.favorites);
  const [filter, setFilter] = useState("");
  const items = getNavigation();
  const matching = items.filter((item) => `${t(item.label, { defaultValue: item.label })} ${item.path}`.toLowerCase().includes(filter.trim().toLowerCase()));
  const pinned = matching.filter((item) => favorites.includes(item.path));
  return (
    <aside className={cn("app-sidebar flex h-full shrink-0 flex-col border-r border-border transition-[width] duration-200", mobile ? "w-full border-r-0" : collapsed ? "w-[68px]" : "w-[248px]")}>
      <div className={cn("flex h-16 shrink-0 items-center gap-2.5 px-4", collapsed && "!px-3")}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-accent-foreground shadow-sm"><Bot size={21} strokeWidth={1.8} /></div>
        {!collapsed && <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold tracking-tight text-heading">{branding.title}</div><div className="text-[11px] text-muted">{t("personalWorkspace")}</div></div>}
      </div>
      {!collapsed ? <div className="mx-3 mb-3 flex items-center gap-2 rounded-lg border border-border bg-card px-2.5">
        <Search size={14} className="shrink-0 text-muted" />
        <input aria-label={t("filterNavigation")} placeholder={t("filterNavigation")} value={filter}
          onChange={(event) => setFilter(event.target.value)} className="h-9 w-full min-w-0 bg-transparent text-xs outline-none placeholder:text-muted" />
      </div> : <Button variant="ghost" size="icon" className="mx-auto mb-3" aria-label={t("search")} onClick={() => setPaletteOpen(true)}><Search size={18} /></Button>}
      <nav aria-label={t("navigation")} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2.5 pb-4">
        {pinned.length > 0 && !filter && !collapsed && <section>
          <h2 className="px-2.5 pb-1.5 text-[10px] font-semibold tracking-wider text-muted">{t("favorites")}</h2>
          {pinned.map((item) => <NavigationLink key={item.path} item={item} collapsed={false} />)}
        </section>}
        {NAVIGATION_GROUPS.map((group) => {
          const groupItems = matching.filter((item) => item.group === group);
          return groupItems.length > 0 && <section key={group}>
            {!collapsed && <h2 className="px-2.5 pb-1.5 text-[10px] font-semibold tracking-wider text-muted">{t(`groups.${group}`)}</h2>}
            <div className="space-y-0.5">{groupItems.map((item) => <NavigationLink key={item.path} item={item} collapsed={collapsed} />)}</div>
          </section>;
        })}
        {matching.length === 0 && <p className="px-3 py-8 text-center text-xs text-muted">{t("noNavigationResults")}</p>}
      </nav>
      <div className="flex h-13 shrink-0 items-center gap-2 border-t border-border px-3">
        {!collapsed && <span className="flex-1 text-[11px] text-muted">AnelfAgent <span className="font-mono">v{branding.version}</span></span>}
        {!mobile && <Button variant="ghost" size="icon" className={cn(collapsed && "mx-auto")}
          onClick={toggleSidebar} aria-label={t(collapsed ? "expandSidebar" : "collapseSidebar")}>
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </Button>}
      </div>
    </aside>
  );
}
