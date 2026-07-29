import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";

export function totalPages(total: number, size: number): number {
  return Math.max(1, Math.ceil(total / size));
}

/** 统一的「上一页 / n / m / 下一页」分页控件 */
export function Pagination({
  page,
  total,
  pageSize,
  onChange,
}: {
  page: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
}) {
  const { t } = useTranslation("common");
  const pages = totalPages(total, pageSize);
  return (
    <div className="flex items-center justify-center gap-3">
      <Button variant="secondary" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        {t("prev")}
      </Button>
      <span className="text-xs text-muted">
        {page} / {pages}
      </span>
      <Button variant="secondary" size="sm" disabled={page >= pages} onClick={() => onChange(page + 1)}>
        {t("next")}
      </Button>
    </div>
  );
}
