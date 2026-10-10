import { useEffect } from "react";

/** 让应用壳与弹层跟随软键盘后的可视区域，缩放时保持浏览器原生行为。 */
export function useVisualViewport(): void {
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const root = document.documentElement;
    const update = () => {
      if (viewport.scale !== 1) return;
      root.style.setProperty("--viewport-height", `${viewport.height}px`);
      root.style.setProperty("--viewport-top", `${viewport.offsetTop}px`);
      const editable = document.activeElement?.matches('input, textarea, [contenteditable="true"]');
      root.dataset.keyboardOpen = String(window.innerHeight - viewport.height > 150 && (!!editable || root.dataset.keyboardOpen === "true"));
    };
    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", update);
    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", update);
      root.style.removeProperty("--viewport-height");
      root.style.removeProperty("--viewport-top");
      delete root.dataset.keyboardOpen;
    };
  }, []);
}
