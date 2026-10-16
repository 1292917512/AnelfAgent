import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { Search, Wrench } from "lucide-react";
import { mcpApi } from "@/lib/api";
import type { MCPToolInfo } from "@/lib/types";
import { Button, Input, Modal } from "@/components/ui";
import { AsyncState } from "@/components/common/AsyncState";

export function ToolsSection({ serverName, toolCount }: { serverName: string; toolCount: number }) {
  const { t } = useTranslation("mcp");
  const [open, setOpen] = useState(false);
  return <>
    <Button size="sm" variant="ghost" onClick={() => setOpen(true)}><Wrench size={13} />{t("nTools", { count: toolCount })}</Button>
    {open && <ToolsDialog serverName={serverName} onClose={() => setOpen(false)} />}
  </>;
}

function ToolsDialog({ serverName, onClose }: { serverName: string; onClose: () => void }) {
  const { t } = useTranslation("mcp");
  const [search, setSearch] = useState("");
  const query = useQuery({ queryKey: ["mcpServerTools", serverName], queryFn: () => mcpApi.tools(serverName).then((r) => r.data), staleTime: 30_000, throwOnError: false });
  const tools = (query.data ?? []).filter((tool) => `${tool.name} ${tool.description}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  return <Modal open onClose={onClose} title={`${serverName} · ${t("showTools")}`} width="max-w-2xl">
    <div className="space-y-4">
      <div className="relative"><Search size={15} className="absolute left-3 top-3 text-muted" />
        <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("searchTools")} placeholder={t("searchTools")} />
      </div>
      <AsyncState pending={query.isPending} error={query.error} retry={() => void query.refetch()}>
        {!tools.length && <p className="py-8 text-center text-sm text-muted">{t("noTools")}</p>}
        {tools.map((tool) => <ToolDetails key={tool.name} tool={tool} />)}
      </AsyncState>
    </div>
  </Modal>;
}

function ToolDetails({ tool }: { tool: MCPToolInfo }) {
  const { t } = useTranslation("mcp");
  const [open, setOpen] = useState(false);
  return <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="rounded-lg border border-border p-3">
    <summary className="cursor-pointer break-all font-mono text-xs font-medium text-heading">{tool.name}
      <span className="ml-2 font-sans font-normal text-muted">{t("nParams", { count: tool.params.length })}</span>
    </summary>
    {open && <>
      <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed text-muted">{tool.description}</p>
      <dl className="mt-3 space-y-2">{tool.params.map((param) => <div key={param.name} className="rounded-md bg-secondary p-2 text-xs">
        <dt className="break-all font-mono text-heading">{param.name} <span className="text-muted">{param.type}</span>{param.required && <span className="ml-2 font-sans text-accent">{t("required")}</span>}</dt>
        <dd className="mt-1 whitespace-pre-wrap break-words text-muted">{param.description}{param.enum && <p className="mt-1 break-all font-mono">{param.enum.join(" · ")}</p>}</dd>
      </div>)}</dl>
    </>}
  </details>;
}
