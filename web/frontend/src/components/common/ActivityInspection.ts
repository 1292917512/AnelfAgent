import { createContext } from "react";

/** 过程查看入口由页面提供，共享行不依赖页面渲染实现。 */
export const ActivityInspection = createContext<((ids: string[]) => void) | null>(null);
