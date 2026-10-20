import { useEffect, useState } from "react";
import type { IdentityListFilters } from "@/lib/types";

/** 管理列表的输入与查询参数分离；关键词提交和页码重置在同一次更新中完成。 */
export function useIdentityFilters() {
  const [keyword, setKeyword] = useState("");
  const [filters, setFilters] = useState<IdentityListFilters>({ status: "", keyword: "", page: 1, pageSize: 50 });
  useEffect(() => {
    const timer = setTimeout(() => setFilters((current) => current.keyword === keyword ? current : { ...current, keyword, page: 1 }), 250);
    return () => clearTimeout(timer);
  }, [keyword]);
  return { filters, keyword, setKeyword, status: filters.status, page: filters.page, pageSize: filters.pageSize,
    setStatus: (status: string) => setFilters((current) => ({ ...current, status, keyword, page: 1 })),
    setPage: (page: number) => setFilters((current) => ({ ...current, page })),
  };
}
