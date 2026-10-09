import { LockKeyhole } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConfigMetaItem } from "@/lib/types";

export function ConfigSource({ item }: { item: ConfigMetaItem }) {
  const { t } = useTranslation("config");
  if (!item.environment_variable) return null;
  return <p className="mt-1 flex items-start gap-1.5 text-xs text-muted">
    <LockKeyhole size={13} className="mt-0.5 shrink-0" />
    <span className="break-all">{t("environmentManaged", { name: item.environment_variable })}</span>
  </p>;
}
