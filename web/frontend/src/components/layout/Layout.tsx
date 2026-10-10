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
import { startUiStateReporting } from "@/lib/ui-state-reporting";
import { useVisualViewport } from "@/hooks/useVisualViewport";

export function Layout() {
  useVisualViewport();
  const { t } = useTranslation("nav");
  const isMobile = useIsMobile();
  const mobileMenuOpen = useAppStore((state) => state.mobileMenuOpen);
  const setMobileMenuOpen = useAppStore((state) => state.setMobileMenuOpen);
  const density = useAppStore((state) => state.density);
  const branding = useAppStore((state) => state.branding);
  const location = useLocation();
  useEffect(() => startUiStateReporting(location.pathname), [location.pathname]);
  const workspace = CORE_ROUTES.some((route) => route.path === location.pathname && route.workspace);
  useEffect(() => { setMobileMenuOpen(false); }, [location.pathname, setMobileMenuOpen]);
  useEffect(() => { document.title = `${branding.title} · ${t(CORE_ROUTES.find((route) => route.path === location.pathname)?.label ?? "personalWorkspace")}`; }, [branding.title, location.pathname, t]);
  return (
    <div className="app-shell flex overflow-hidden" data-density={density}>
      <a href="#main-content" className="skip-link">{t("skipToContent")}</a>
      {isMobile ? <DialogSurface open={mobileMenuOpen} onClose={() => setMobileMenuOpen(false)} title={t("navigation")} placement="left" className="mobile-menu">
        <Button variant="ghost" size="icon" className="absolute right-2 top-4 z-10" onClick={() => setMobileMenuOpen(false)} aria-label={t("common:close")}><X size={16} /></Button>
        <Sidebar mobile />
      </DialogSurface> : <Sidebar />}
      <div className="app-frame flex min-w-0 flex-1 flex-col">
        <Header />
        <main id="main-content" tabIndex={-1} className={cn("min-h-0 flex-1 outline-none", workspace ? "overflow-hidden" : "page-scroll overflow-y-auto overscroll-contain px-4 py-6 md:px-8 md:py-8")}>
          <Outlet />
        </main>
        <MobileNav />
      </div>
    </div>
  );
}
