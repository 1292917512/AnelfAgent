import { lazy, Suspense, useEffect } from "react";
import { useAppStore } from "@/stores/app-store";
import { useTranslation } from "react-i18next";
import { DialogSurface } from "@/components/ui/DialogSurface";

const CommandPalette = lazy(() => import("./CommandPalette").then((module) => ({ default: module.CommandPalette })));

/** 常驻全局快捷键；完整命令面板仅在首次打开时加载。 */
export function CommandPaletteHost() {
  const open = useAppStore((state) => state.paletteOpen);
  const setOpen = useAppStore((state) => state.setPaletteOpen);
  const { t } = useTranslation(["palette", "common"]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (event.repeat || event.isComposing) return;
        const { paletteOpen, setPaletteOpen } = useAppStore.getState();
        setPaletteOpen(!paletteOpen);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return <DialogSurface open={open} onClose={() => setOpen(false)} title={t("label")} placement="top" className="command-palette max-w-xl overflow-hidden">
    <Suspense fallback={<p role="status" className="p-4 text-sm text-muted">{t("common:loading")}</p>}><CommandPalette /></Suspense>
  </DialogSurface>;
}
