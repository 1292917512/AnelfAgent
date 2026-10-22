import { useCallback, useState } from "react";

/** 按需挂载的编辑/详情会话：通过校验后关闭弹层，焦点恢复后才通知宿主卸载草稿。 */
export function useDialogSession(onAfterClose: () => void) {
  const [open, setOpen] = useState(true);
  const close = useCallback(() => setOpen(false), []);
  return { close, surfaceProps: { open, onAfterClose } };
}
