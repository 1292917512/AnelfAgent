import { useEffect, useState } from "react";

/** 响应式媒体查询 hook（移动端 < 768px） */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window !== "undefined" ? window.matchMedia(query).matches : false,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e: MediaQueryListEvent) => setMatches(e.matches);
    mql.addEventListener("change", onChange);
    setMatches(mql.matches);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);

  return matches;
}

/** 是否为移动端视口（< 768px） */
export function useIsMobile(): boolean {
  return useMediaQuery("(max-width: 767px)");
}
