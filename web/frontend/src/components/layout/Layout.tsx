import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { X } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { MobileNav } from "./MobileNav";
import { DialogSurface } from "@/components/ui/DialogSurface";
import { Button } from "@/components/ui/Button";
import { useIsMobile } from "@/lib/use-media-query";
import { useAppStore } from "@/stores/app-store";
import { cn } from "@/lib/utils";
import { CORE_ROUTES } from "@/lib/core-routes";

export function Layout() {
  const { t } = useTranslation("nav");
  const isMobile = useIsMobile();
  const mobileMenuOpen = useAppStore((state) => state.mobileMenuOpen);
  const setMobileMenuOpen = useAppStore((state) => state.setMobileMenuOpen);
  const density = useAppStore((state) => state.density);
  const branding = useAppStore((state) => state.branding);
  const location = useLocation();
  const workspace = CORE_ROUTES.some((route) => route.path === location.pathname && route.workspace);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname, setMobileMenuOpen]);
  useEffect(() => { document.title = `${branding.title} · ${t(CORE_ROUTES.find((route) => route.path === location.pathname)?.label ?? "personalWorkspace")}`; }, [branding.title, location.pathname, t]);
  return (
    <div className="flex h-dvh overflow-hidden" data-density={density}>
      <a href="#main-content" className="skip-link">{t("skipToContent")}</a>
      {isMobile ? <DialogSurface open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} title={t("navigation")} placement="left" className="max-w-[280px]">
        <Button variant="ghost" size="icon" className="absolute right-2 top-4 z-10" onClick={() => setMobileMenuOpen(false)} aria-label={t("common:close")}><X size={16} /></Button>
        <Sidebar mobile />
      </DialogSurface> : <Sidebar />}
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <main id="main-content" tabIndex={-1} className={cn("min-h-0 flex-1 outline-none", workspace ? "overflow-hidden" : "overflow-y-auto px-4 py-6 md:px-7 md:py-7")}>
          <Outlet />
        </main>
        <MobileNav />
      </div>
    </div>
  );
}
