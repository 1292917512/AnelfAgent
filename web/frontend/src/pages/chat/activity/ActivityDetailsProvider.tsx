import { lazy, Suspense, useState, type ReactNode } from "react";
import { ActivityInspection } from "@/components/common/ActivityInspection";

const ActivityDetailsDialog = lazy(() => import("./ActivityDetailsDialog"));

/** 按需加载过程查看器，关闭后释放详情组件。 */
export function ActivityDetailsProvider({ children }: { children: ReactNode }) {
  const [ids, setIds] = useState<string[]>([]);
  return <ActivityInspection.Provider value={setIds}>
    {children}
    {ids.length > 0 && <Suspense fallback={null}><ActivityDetailsDialog ids={ids} onClose={() => setIds([])} onReveal={(id) => setIds([id])} /></Suspense>}
  </ActivityInspection.Provider>;
}
