import { matchPath, NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Menu } from "lucide-react";
import { getNavigation } from "@/lib/navigation";
import { useAppStore } from "@/stores/app-store";
import { cn } from "@/lib/utils";

export function MobileNav() {
  const { t } = useTranslation("nav");
  const openMenu = useAppStore((state) => state.setMobileMenuOpen);
  const menuOpen = useAppStore((state) => state.mobileMenuOpen);
  const { pathname } = useLocation();
  const items = getNavigation().filter((item) => item.mobile);
  const secondaryPage = !items.some((item) => matchPath(item.path, pathname));
  return (
    <nav aria-label={t("navigation")} className="mobile-nav flex shrink-0 items-stretch border-t border-border bg-panel md:hidden safe-area-bottom">
      {items.map((item) => <NavLink key={item.path} to={item.path} end={item.path === "/"}
        className={({ isActive }) => cn("mobile-nav-item flex min-h-14 flex-1 flex-col items-center justify-center gap-1 text-[10px] font-medium", isActive ? "is-active text-accent" : "text-muted")}>
        <item.icon size={19} strokeWidth={1.8} /><span>{t(item.label)}</span>
      </NavLink>)}
      <button type="button" onClick={() => openMenu(true)} aria-haspopup="dialog" aria-expanded={menuOpen}
        className={cn("mobile-nav-item flex min-h-14 flex-1 flex-col items-center justify-center gap-1 text-[10px] font-medium", secondaryPage ? "is-active text-accent" : "text-muted")}>
        <Menu size={19} /><span>{t("more")}</span>
      </button>
    </nav>
  );
}
