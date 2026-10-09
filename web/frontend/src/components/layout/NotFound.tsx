import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";

export function NotFound() {
  const { t } = useTranslation("common");
  return <EmptyState icon={Compass} title={t("pageNotFound")} description={t("pageNotFoundDescription")}
    action={<Link to="/" className="inline-flex rounded-lg bg-accent px-4 py-2 text-sm font-medium text-accent-foreground">{t("backToWorkspace")}</Link>} />;
}
