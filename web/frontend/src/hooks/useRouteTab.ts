import { useSearchParams } from "react-router-dom";

export function useRouteTab<T extends string>(tabs: readonly T[], defaultTab: T, key = "tab") {
  const [params, setParams] = useSearchParams();
  const active = tabs.find((tab) => tab === params.get(key)) ?? defaultTab;
  const setActive = (tab: T) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      if (tab === defaultTab) next.delete(key);
      else next.set(key, tab);
      return next;
    });
  };
  return [active, setActive] as const;
}
