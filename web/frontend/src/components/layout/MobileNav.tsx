import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Menu } from "lucide-react";
import { getNavigation } from "@/lib/navigation";
import { useAppStore } from "@/stores/app-store";
import { cn } from "@/lib/utils";

export function MobileNav() {
  const { t } = useTranslation("nav");
  const openMenu = useAppStore((state) => state.setMobileMenuOpen);
  return (
    <nav aria-label={t("navigation")} className="mobile-nav flex shrink-0 items-stretch border-t border-border bg-panel md:hidden safe-area-bottom">
      {getNavigation().filter((item) => item.mobile).map((item) => <NavLink key={item.path} to={item.path} end={item.path === "/"}
        className={({ isActive }) => cn("mobile-nav-item flex min-h-14 flex-1 flex-col items-center justify-center gap-1 text-[10px] font-medium", isActive ? "is-active text-accent" : "text-muted")}>
        <item.icon size={19} strokeWidth={1.8} /><span>{t(item.label)}</span>
      </NavLink>)}
      <button type="button" onClick={() => openMenu(true)} className="flex min-h-14 flex-1 flex-col items-center justify-center gap-1 text-[10px] font-medium text-muted">
        <Menu size={19} /><span>{t("more")}</span>
      </button>
    </nav>
  );
}
