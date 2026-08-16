import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { nonebotApi } from "@/lib/api";

/** 安装进行中横幅（适配器/商店面板共用：任何包操作进行时置顶提示） */
export function InstallProgressBanner() {
  const { t } = useTranslation("nonebot");
  const { data: status } = useQuery({
    queryKey: ["nonebotStatus"],
    queryFn: () => nonebotApi.status().then((r) => r.data),
    refetchInterval: 3000,
  });

  const install = status?.install;
  if (!install?.running) return null;

  return (
    <div className="rounded-lg border border-warn/30 bg-warn-subtle p-3">
      <div className="flex items-center gap-2 text-sm text-warn">
        <RefreshCw size={14} className="animate-spin" />
        {t("runtime.installing", { packages: (install.packages || []).join(", ") })}
      </div>
      {(install.logs || []).slice(-3).map((line, i) => (
        <div key={i} className="mt-1 truncate font-mono text-[11px] text-muted">{line}</div>
      ))}
    </div>
  );
}
