import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { mcpApi } from "@/lib/api";
import { Badge, Spinner } from "@/components/ui";
import { cn } from "@/lib/utils";

/** 服务器工具区：默认折叠，展开时按需拉取工具详情（描述 + 参数） */
export function ToolsSection({
  serverName,
  toolCount,
}: {
  serverName: string;
  toolCount: number;
}) {
  const { t } = useTranslation("mcp");
  const [expanded, setExpanded] = useState(false);

  const { data: tools, isLoading, isError } = useQuery({
    queryKey: ["mcpServerTools", serverName],
    queryFn: () => mcpApi.tools(serverName).then((r) => r.data),
    enabled: expanded,
    staleTime: 30_000,
  });

  return (
    <div className="mt-3 border-t border-border pt-2.5">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-muted hover:text-foreground transition-colors"
      >
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Wrench size={12} />
        {expanded ? t("hideTools") : t("showTools")}
        <span className="text-muted/70">({toolCount})</span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2">
          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-muted py-1">
              <Spinner size={13} />
              {t("common:loading")}
            </div>
          )}
          {isError && (
            <p className="text-xs text-danger">{t("toolsLoadFailed")}</p>
          )}
          {tools && tools.length === 0 && (
            <p className="text-xs text-muted">{t("noTools")}</p>
          )}
          {tools?.map((tool) => (
            <div
              key={tool.name}
              className="rounded-md border border-border bg-elevated px-3 py-2"
            >
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-medium font-mono text-heading">
                  {tool.name}
                </span>
                {tool.params.length > 0 && (
                  <Badge variant="neutral">
                    {tool.params.length} params
                  </Badge>
                )}
              </div>
              {tool.description && (
                <p className="text-[11px] text-muted mt-1 leading-relaxed">
                  {tool.description}
                </p>
              )}
              {tool.params.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {tool.params.map((p) => (
                    <span
                      key={p.name}
                      title={p.description || p.name}
                      className={cn(
                        "text-[10px] px-1.5 py-0.5 rounded font-mono border",
                        p.required
                          ? "border-accent/30 text-accent bg-accent-subtle"
                          : "border-border text-muted bg-secondary",
                      )}
                    >
                      {p.name}
                      <span className="opacity-60">:{p.type}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
