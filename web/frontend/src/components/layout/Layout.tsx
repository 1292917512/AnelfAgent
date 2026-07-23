import { useEffect } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { Header } from "./Header";
import { MobileNav } from "./MobileNav";
import { useIsMobile } from "@/lib/use-media-query";
import { useAppStore } from "@/stores/app-store";
import { cn } from "@/lib/utils";

export function Layout() {
  const isMobile = useIsMobile();
  const mobileMenuOpen = useAppStore((s) => s.mobileMenuOpen);
  const setMobileMenuOpen = useAppStore((s) => s.setMobileMenuOpen);
  const location = useLocation();

  // 路由切换时关闭移动端抽屉
  useEffect(() => {
    setMobileMenuOpen(false);
  }, [location.pathname, setMobileMenuOpen]);

  return (
    <div className="flex h-dvh overflow-hidden">
      {/* 桌面端：固定侧边栏；移动端：抽屉 + 遮罩 */}
      {isMobile ? (
        <>
          {mobileMenuOpen && (
            <div
              className="fixed inset-0 z-40 bg-black/50"
              onClick={() => setMobileMenuOpen(false)}
            />
          )}
          <div
            className={cn(
              "fixed inset-y-0 left-0 z-50 w-64 transform transition-transform duration-200",
              mobileMenuOpen ? "translate-x-0" : "-translate-x-full",
            )}
          >
            <Sidebar />
          </div>
        </>
      ) : (
        <Sidebar />
      )}

      <div className="flex flex-col flex-1 min-w-0">
        <Header />
        <main className="flex-1 overflow-y-auto p-3 md:p-6">
          <Outlet />
        </main>
        <MobileNav />
      </div>
    </div>
  );
}
