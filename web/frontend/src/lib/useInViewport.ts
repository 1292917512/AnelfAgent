import { useEffect, useRef, useState } from "react";

/**
 * IntersectionObserver 懒挂载：元素进入视口才置 true（一次性，进入后不再回退）。
 * 用于聊天历史中的图片/视频等媒体延迟挂载，避免翻页时全部并发请求。
 */
export function useInViewport<T extends HTMLElement = HTMLDivElement>(rootMargin = "200px") {
  const ref = useRef<T>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (inView) return;
    const el = ref.current;
    if (!el) return;
    if (typeof IntersectionObserver === "undefined") {
      setInView(true);
      return;
    }
    const ob = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setInView(true);
            ob.disconnect();
            break;
          }
        }
      },
      { rootMargin },
    );
    ob.observe(el);
    return () => ob.disconnect();
  }, [inView, rootMargin]);

  return { ref, inView };
}
