import { useLayoutEffect, useRef } from "react";
import { useNavigationType, useSearchParams } from "react-router-dom";

/** 合并尚未完成导航的参数写入；路由确认或外部导航后释放待确认记录。 */
export function useSearchParamUpdates() {
  const [params, setParams] = useSearchParams();
  const navigation = useNavigationType();
  const latest = useRef(params);
  const pending = useRef(new Set<string>());
  useLayoutEffect(() => {
    const value = params.toString();
    if (navigation === "POP" || !pending.current.has(value)) {
      latest.current = params;
      pending.current.clear();
    } else {
      pending.current.delete(value);
      if (value === latest.current.toString()) pending.current.clear();
    }
  }, [params, navigation]);
  const update = (mutate: (next: URLSearchParams) => void) => {
    const next = new URLSearchParams(latest.current);
    mutate(next);
    latest.current = next;
    pending.current.add(next.toString());
    setParams(next, { replace: true });
  };
  return [params, update] as const;
}
