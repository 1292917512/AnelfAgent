import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui";
import { pluginsApi } from "./api";
import type { PluginSource } from "./types";

export function useInspection(target: PluginSource, auto: boolean) {
  return useQuery({
    queryKey: ["plugin-inspection", target],
    queryFn: ({ signal }) => pluginsApi.inspect(target, signal).then((r) => r.data),
    enabled: auto,
    retry: false,
    throwOnError: false,
    refetchOnWindowFocus: false,
  });
}

export function Inspection({ query, disabled }: { query: ReturnType<typeof useInspection>; disabled?: boolean }) {
  const { t } = useTranslation("plugins");
  const result = query.data;
  return <section className="space-y-2 rounded-lg border border-border p-3 text-sm">
    <div className="flex items-center justify-between gap-2">
      <h3 className="font-medium">{t("inspection")}</h3>
      <Button type="button" size="sm" loading={query.isFetching} disabled={disabled}
        onClick={() => void query.refetch()}>{t(result || query.error ? "inspectAgain" : "inspect")}</Button>
    </div>
    {query.error && <p role="alert" className="text-danger">{query.error.message}</p>}
    {result && (result.status === "verified" ? <>
      <p>{[result.name, result.version, t(result.format === "dsh-host" ? "formatHost" : result.format === "dsh" ? "formatMcp" : "formatNative")].filter(Boolean).join(" · ")}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        {([
          ["requirements", Object.entries(result.requirements).map(([name, version]) => name + " " + version)],
          ["skills", result.skills], ["commands", result.commands], ["tools", [result.tools_file].filter(Boolean)],
          ["mcpServers", result.mcp_servers], ["revision", [result.revision.slice(0, 12)]],
        ] as const).map(([label, values]) => values.length > 0 && <div key={label} className="contents">
          <dt className="text-muted">{t(label)}</dt><dd className="break-all">{values.join(", ")}</dd>
        </div>)}
      </dl>
      {result.format === "dsh-host" && <p className="text-xs text-muted">{result.host_reason} {t("hostCheck")}</p>}
    </> : <p role="status" className="break-words">{t(result.status)} · {result.detail}</p>)}
  </section>;
}
