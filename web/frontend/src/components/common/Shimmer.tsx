/**
 * shimmer 扫光文字 — 运行态文案（"思考中…"）的注意力引导。
 *
 * ZCode .animated-gradient-text 的移植：300% 宽线性渐变 + background-clip:text
 * 4s 扫过；深浅主题各用一组 strong/soft 变量（styles 注释明确"深底只需换变量"）。
 * reduced-motion 下静止（媒体查询由组件内 matchMedia 判定降级为静态色）。
 */

import { useEffect, useState, type ReactNode } from "react";

export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () => typeof window !== "undefined"
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

export function Shimmer({ children, className }: { children: ReactNode; className?: string }) {
  const reduced = usePrefersReducedMotion();
  return (
    <span
      className={(className ? className + " " : "") + (reduced ? "text-muted" : "anelf-shimmer-text")}
    >
      {children}
    </span>
  );
}
