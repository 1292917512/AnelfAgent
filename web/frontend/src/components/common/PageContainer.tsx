import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { findRoute } from "@/lib/navigation";
import { cn } from "@/lib/utils";

export function PageContainer({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("page-container", className)}>{children}</div>;
}

export function PageHeader({ icon, title, subtitle, actions }: {
  icon?: ReactNode; title: ReactNode; subtitle?: ReactNode; actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5">
          {icon && <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border bg-card text-accent [&>svg]:h-[18px] [&>svg]:w-[18px]">{icon}</span>}
          <h1 className="text-xl font-semibold tracking-tight text-heading md:text-[23px]">{title}</h1>
        </div>
        {subtitle && <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function PageIntro({ actions }: { actions?: ReactNode }) {
  const { t } = useTranslation("nav");
  const route = findRoute(useLocation().pathname);
  if (!route) return null;
  return <PageHeader icon={<route.icon />} title={t(route.label)} subtitle={t(`descriptions.${route.label}`)} actions={actions} />;
}
