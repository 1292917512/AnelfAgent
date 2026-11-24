import { useEffect, useRef, useState } from "react";

/** 读取容器实际尺寸，并跟随布局变化。 */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 先直接量一次——部分 WebView 中 ResizeObserver 回调可能不触发，不能只靠它
    const measure = ({ width, height }: DOMRectReadOnly) =>
      setSize((previous) => previous?.width === width && previous.height === height ? previous : { width, height });
    const measureElement = () => measure(el.getBoundingClientRect());
    measureElement();
    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", measureElement);
      return () => window.removeEventListener("resize", measureElement);
    }
    const observer = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) measure(r);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, size };
}
