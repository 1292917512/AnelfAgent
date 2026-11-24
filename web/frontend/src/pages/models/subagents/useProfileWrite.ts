import { useRef } from "react";
import { useMutation } from "@tanstack/react-query";

/** 每次显式提交只发一个请求，失败后由用户用当前草稿重试。 */
export function useProfileWrite<T>(write: (value: T) => Promise<unknown>, onSuccess: () => void | Promise<void>) {
  const sending = useRef(false);
  const mutation = useMutation({
    mutationFn: write, onSuccess, retry: false,
    onSettled: () => { sending.current = false; },
  });
  function submit(value: T) {
    if (sending.current) return;
    sending.current = true;
    mutation.mutate(value);
  }
  return { submit, pending: mutation.isPending, error: mutation.error, submitted: mutation.variables };
}
