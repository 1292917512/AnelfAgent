import { lazy, Suspense, useEffect } from "react";
import { useAppStore } from "@/stores/app-store";

const CommandPalette = lazy(() => import("./CommandPalette").then((module) => ({ default: module.CommandPalette })));

/** 常驻全局快捷键；完整命令面板仅在首次打开时加载。 */
export function CommandPaletteHost() {
  const open = useAppStore((state) => state.paletteOpen);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        const { paletteOpen, setPaletteOpen } = useAppStore.getState();
        setPaletteOpen(!paletteOpen);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  if (!open) return null;
  return <Suspense fallback={null}><CommandPalette /></Suspense>;
}
