import { useId } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Select } from "@/components/ui";
import type { ProxyMode } from "@/lib/types";

export function NetworkConfigLink() {
  const { t } = useTranslation("config");
  return <Link to="/config?key=proxy_enabled" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-accent hover:underline">
    {t("network.configure")}<ArrowUpRight size={13} />
  </Link>;
}

export function NetworkModeField({ value, onChange }: { value: ProxyMode; onChange: (mode: ProxyMode) => void }) {
  const { t } = useTranslation("config");
  const id = useId();
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <label htmlFor={id} className="text-xs font-medium text-muted">{t("network.mode")}</label>
      <NetworkConfigLink />
    </div>
    <Select id={id} className="w-full" value={value} onChange={(event) => {
      const mode = event.target.value;
      if (mode === "system" || mode === "direct" || mode === "proxy") onChange(mode);
    }}>
      {(["system", "direct", "proxy"] as const).map((mode) => <option key={mode} value={mode}>{t(`network.${mode}`)}</option>)}
    </Select>
    <p className="text-xs leading-relaxed text-muted">{t("network.hint")}</p>
  </div>;
}
