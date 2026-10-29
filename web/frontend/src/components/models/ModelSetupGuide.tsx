import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "react-router-dom";
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
  const configuring = pathname === "/models";
  const status = useQuery(runtimeStatusQuery());
  const priorities = useQuery({
    ...modelPrioritiesQuery(),
    enabled: !configuring && status.isSuccess && status.data.ready,
  });
  if (configuring || !status.isSuccess || !status.data.ready || !priorities.isSuccess || priorities.isFetching
    || priorities.data.chat?.some((model) => model.enabled)) return null;

  return <aside role="status" aria-labelledby={titleId} className="flex shrink-0 flex-wrap items-center gap-3 border-b border-accent/20 bg-accent-subtle px-4 py-3 md:px-6">
    <Cpu size={20} className="shrink-0 text-accent" aria-hidden="true" />
    <div className="min-w-0 flex-1 basis-56">
      <h2 id={titleId} className="text-sm font-semibold text-heading">{t("setupGuide.title")}</h2>
      <p className="mt-1 text-xs leading-relaxed text-muted">{t("setupGuide.description")}</p>
    </div>
    <ConfigLink config={{ page: "models" }} className="min-h-11 shrink-0 rounded-lg bg-accent px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-accent-hover hover:no-underline">
      {t("setupGuide.action")}<ArrowRight size={15} aria-hidden="true" />
    </ConfigLink>
  </aside>;
}
