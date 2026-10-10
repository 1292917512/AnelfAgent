import { useEffect, useRef, useState } from "react";
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
  const pinned = useAppStore((state) => !state.sidebarCollapsed);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelHover = () => {
    if (hoverTimer.current !== null) clearTimeout(hoverTimer.current);
    hoverTimer.current = null;
  };
  useEffect(() => () => { if (hoverTimer.current !== null) clearTimeout(hoverTimer.current); }, []);
  const collapsed = !mobile && !pinned && !hovered && !focused;
  const toggleSidebar = useAppStore((state) => state.toggleSidebar);
  const setPaletteOpen = useAppStore((state) => state.setPaletteOpen);
  const branding = useAppStore((state) => state.branding);
  const favorites = useAppStore((state) => state.favorites);
  const [filter, setFilter] = useState("");
  const items = getNavigation();
  const matching = items.filter((item) => `${t(item.label, { defaultValue: item.label })} ${item.path}`.toLowerCase().includes(filter.trim().toLowerCase()));
  const favoriteItems = matching.filter((item) => favorites.includes(item.path));
  return (
    <div className={cn("relative h-full shrink-0", mobile ? "w-full" : pinned ? "w-[248px]" : "w-[68px]")}>
    <aside
      onPointerMove={(event) => {
        if (event.pointerType !== "mouse" || mobile || pinned || hovered || hoverTimer.current !== null) return;
        hoverTimer.current = setTimeout(() => { hoverTimer.current = null; setHovered(true); }, 180);
      }}
      onPointerLeave={() => { cancelHover(); setHovered(false); }}
      onFocusCapture={(event) => { if (event.target.matches(":focus-visible")) setFocused(true); }}
      onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false); }}
      onKeyDown={(event) => { if (event.key === "Escape") { cancelHover(); setHovered(false); setFocused(false); } }}
      onClickCapture={(event) => { if (event.target instanceof Element && event.target.closest("a")) { cancelHover(); setHovered(false); setFocused(false); } }}
      className={cn("app-sidebar flex h-full flex-col border-r border-border transition-[width,box-shadow] duration-200", mobile ? "w-full border-r-0" : "absolute inset-y-0 left-0 z-40", collapsed ? "w-[68px]" : "w-[248px]", !mobile && !pinned && !collapsed && "shadow-2xl")}>
      <div className={cn("flex h-16 shrink-0 items-center gap-2.5 px-4", collapsed && "!px-3")}>
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-accent-foreground shadow-sm"><Bot size={21} strokeWidth={1.8} /></div>
        {!collapsed && <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold tracking-tight text-heading">{branding.title}</div><div className="text-[11px] text-muted">{t("personalWorkspace")}</div></div>}
      </div>
      <div className={cn("mx-3 mb-3 flex h-9 shrink-0 items-center rounded-lg", collapsed ? "justify-center" : "border border-border bg-card pr-2.5")}>
        <Button variant="ghost" size="icon" className="shrink-0" aria-label={t("search")} onClick={() => setPaletteOpen(true)}><Search size={18} /></Button>
        {!collapsed && <input aria-label={t("filterNavigation")} placeholder={t("filterNavigation")} value={filter}
          onChange={(event) => setFilter(event.target.value)} className="h-9 w-full min-w-0 bg-transparent text-xs outline-none placeholder:text-muted" />}
      </div>
      <nav aria-label={t("navigation")} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-2.5 pb-4">
        {favoriteItems.length > 0 && !filter && <section>
          <h2 className={cn("h-5 px-2.5 pb-1.5 text-[10px] font-semibold tracking-wider text-muted", collapsed && "invisible")}>{t("favorites")}</h2>
          {favoriteItems.map((item) => <NavigationLink key={item.path} item={item} collapsed={collapsed} />)}
        </section>}
        {NAVIGATION_GROUPS.map((group) => {
          const groupItems = matching.filter((item) => item.group === group);
          return groupItems.length > 0 && <section key={group}>
            <h2 className={cn("h-5 px-2.5 pb-1.5 text-[10px] font-semibold tracking-wider text-muted", collapsed && "invisible")}>{t(`groups.${group}`)}</h2>
            <div className="space-y-0.5">{groupItems.map((item) => <NavigationLink key={item.path} item={item} collapsed={collapsed} />)}</div>
          </section>;
        })}
        {matching.length === 0 && <p className="px-3 py-8 text-center text-xs text-muted">{t("noNavigationResults")}</p>}
      </nav>
      <div className="flex h-13 shrink-0 items-center gap-2 border-t border-border px-3">
        {!collapsed && <span className="flex-1 text-[11px] text-muted">AnelfAgent <span className="font-mono">v{branding.version}</span></span>}
        {!mobile && <Button variant="ghost" size="icon" className={cn(collapsed && "mx-auto")}
          onClick={toggleSidebar} aria-pressed={pinned} aria-label={t(pinned ? "collapseSidebar" : "expandSidebar")}>
          {pinned ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
        </Button>}
      </div>
    </aside>
    </div>
  );
}
