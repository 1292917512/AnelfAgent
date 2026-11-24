import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { matchPath, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Cpu } from "lucide-react";
import { ConfigLink } from "@/components/common/ConfigLink";
import { modelPrioritiesQuery } from "@/lib/model-queries";
import { runtimeStatusQuery } from "@/lib/runtime-queries";

/** 服务就绪且确认没有启用的对话模型时，引导进入模型配置；不打断当前页面。 */
export function ModelSetupGuide() {
  const { t } = useTranslation("models");
  const titleId = useId();
  const { pathname } = useLocation();
  const configuring = !!matchPath("/models", pathname);
  const status = useQuery(runtimeStatusQuery());
  const priorities = useQuery({
    ...modelPrioritiesQuery(),
    enabled: !configuring && status.isSuccess && status.data.ready,
  });
  if (configuring || !status.isSuccess || !status.data.ready || !priorities.isSuccess
    || priorities.data.chat?.some((model) => model.enabled)) return null;

  return <aside aria-labelledby={titleId} className="model-setup-guide flex shrink-0 items-center gap-2 border-b border-accent/20 bg-accent-subtle px-3 py-1 sm:gap-3 sm:px-6 sm:py-3">
    <Cpu size={20} className="shrink-0 text-accent" aria-hidden="true" />
    <div className="min-w-0 flex-1">
      <h2 id={titleId} className="text-sm font-semibold text-heading">{t("setupGuide.title")}</h2>
      <p className="sr-only mt-1 text-xs leading-relaxed text-muted sm:not-sr-only">{t("setupGuide.description")}</p>
    </div>
    <ConfigLink config={{ page: "models" }} className="min-h-11 max-w-[52%] justify-center rounded-lg px-2 py-2 text-xs font-medium sm:bg-accent sm:px-3 sm:text-sm sm:text-primary-foreground sm:hover:bg-accent-hover sm:hover:no-underline">
      {t("setupGuide.action")}<ArrowRight size={15} aria-hidden="true" />
    </ConfigLink>
  </aside>;
}
