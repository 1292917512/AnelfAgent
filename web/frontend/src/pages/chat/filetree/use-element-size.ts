import { useEffect, useRef, useState } from "react";

/** 监听元素尺寸供虚拟化树填充父容器：挂载时同步测量一次，之后经 ResizeObserver 跟随变化 */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // 先直接量一次——部分 WebView 中 ResizeObserver 回调可能不触发，不能只靠它
    const rect = el.getBoundingClientRect();
    setSize({ width: rect.width, height: rect.height });
    const observer = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r) setSize({ width: r.width, height: r.height });
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, size };
}
