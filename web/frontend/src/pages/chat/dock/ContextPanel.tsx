import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MonitorTab } from "@/pages/context/MonitorTab";

export function ContextPanel() {
  const { t } = useTranslation("context");
  return <div className="p-3">
    <Link to="/context" className="mb-4 flex items-center justify-between text-sm font-medium text-heading">{t("title")}<ArrowUpRight size={15} /></Link>
    <MonitorTab />
  </div>;
}
