import { useCallback, useEffect, useRef, useState } from "react";

/**
 * "copied / saved + setTimeout 自动复位" 反馈 hook。
 *
 * - trigger()：置为 true 并在 duration ms 后自动复位（重复触发会重新计时）
 * - reset()：立即复位
 * - 组件卸载时自动清理定时器，避免 setState on unmounted 警告
 */
export function useCopyFeedback(duration = 2000): [boolean, () => void, () => void] {
  const [active, setActive] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clear = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const trigger = useCallback(() => {
    clear();
    setActive(true);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setActive(false);
    }, duration);
  }, [clear, duration]);

  const reset = useCallback(() => {
    clear();
    setActive(false);
  }, [clear]);

  useEffect(() => clear, [clear]);

  return [active, trigger, reset];
}
