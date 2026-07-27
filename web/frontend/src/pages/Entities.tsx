import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { entitiesApi } from "@/lib/api";
import { StatusDot } from "@/components/common/StatusDot";
import { cn } from "@/lib/utils";
import type { EntityListItem } from "@/lib/types";
import {
  Globe, Image, Terminal, Box, Cpu, FileText,
  MessageSquare, Sticker, FolderTree, Server, Boxes,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  globe: Globe,
  image: Image,
  terminal: Terminal,
  box: Box,
  cpu: Cpu,
  "file-text": FileText,
  "message-square": MessageSquare,
  sticker: Sticker,
  "folder-tree": FolderTree,
  server: Server,
  boxes: Boxes,
};

export default function Entities() {
  const { t } = useTranslation("entities");
  const navigate = useNavigate();

  const { data: entities } = useQuery({
    queryKey: ["entities-list"],
    queryFn: () => entitiesApi.list().then((r) => r.data as EntityListItem[]),
  });

  // 按 group 去重（一个 group 可能有多个实体，取第一个的 manifest）
  const groups = new Map<string, EntityListItem>();
  for (const e of entities ?? []) {
    if (!groups.has(e.group)) {
      groups.set(e.group, e);
    }
  }
  const groupList = Array.from(groups.values());

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 md:px-6 py-4 border-b border-border">
        <h1 className="text-lg font-semibold text-heading">{t("title")}</h1>
        <p className="text-xs text-muted mt-0.5">{t("subtitle")}</p>
      </div>

      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {groupList.map((entity) => {
            const manifest = entity.manifest;
            const Icon = ICON_MAP[manifest?.icon ?? "box"] ?? Box;
            return (
              <button
                key={entity.group}
                onClick={() => navigate(`/entities/${encodeURIComponent(entity.group)}`)}
                className={cn(
                  "flex items-start gap-3 p-4 rounded-lg border text-left transition-all",
                  "bg-card border-border hover:border-border-strong hover:shadow-md",
                  "animate-rise",
                )}
              >
                <div className="p-2 rounded-md bg-accent-subtle text-accent">
                  <Icon size={18} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-heading truncate">
                      {manifest?.display_name || entity.group}
                    </span>
                    <StatusDot status={entity.enabled ? "ok" : "offline"} />
                  </div>
                  <p className="text-[11px] text-muted mt-0.5 line-clamp-2">
                    {manifest?.description || entity.description}
                  </p>
                  <div className="flex items-center gap-2 mt-2 text-[10px] text-muted">
                    <span className="font-mono">{entity.group}</span>
                    {manifest?.version && <span>v{manifest.version}</span>}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
