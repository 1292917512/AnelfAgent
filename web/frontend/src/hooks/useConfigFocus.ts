import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { configItemId } from "@/lib/config-navigation";

/** 在配置项渲染后定位并恢复键盘焦点，同地址再次导航也会重新定位。 */
export function useConfigFocus(key: string | null, ready: boolean, revision: unknown): void {
  const location = useLocation();
  const focused = useRef<{ navigation: string; key: string } | null>(null);
  useEffect(() => {
    if (focused.current?.navigation !== location.key) focused.current = null;
    if (!key || !ready || focused.current?.key === key) return;
    const frame = requestAnimationFrame(() => {
      const element = document.getElementById(configItemId(key));
      if (!element) return;
      focused.current = { navigation: location.key, key };
      element.focus({ preventScroll: true });
      element.scrollIntoView({ block: "center", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
    });
    return () => cancelAnimationFrame(frame);
  }, [key, ready, revision, location.key]);
}
