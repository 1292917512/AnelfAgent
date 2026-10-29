import { Link, type LinkProps } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { configHref } from "@/lib/config-navigation";
import type { ConfigTarget } from "@/lib/types";
import { cn } from "@/lib/utils";

/** 通用配置入口，保留 Router 的历史、键盘、新标签页与 basename 语义。 */
export function ConfigLink({ config, children, className, ...props }: Omit<LinkProps, "to"> & { config: ConfigTarget }) {
  const { t } = useTranslation("config");
  return <Link {...props} to={configHref(config)} className={cn("inline-flex max-w-full items-center gap-1 text-xs text-accent hover:underline", className)}>
    {children ?? <>{t("navigation.open")}<ArrowUpRight size={13} aria-hidden="true" /></>}
  </Link>;
}
