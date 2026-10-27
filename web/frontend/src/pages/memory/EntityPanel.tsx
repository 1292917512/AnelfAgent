import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { X, Link2, Unlink } from "lucide-react";
import { ProfileEditor } from "./ProfileEditor";
import { ProfileTimeline } from "./ProfileTimeline";

function extractEntitySummary(personality: string | undefined): string {
  if (!personality) return "";
  for (const line of personality.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const nameMatch = trimmed.match(/名称|昵称|name|nickname/i);
    if (nameMatch) {
      const val = trimmed.replace(/^[-*]\s*/, "").split(/[：:]/)[1]?.trim();
      if (val) return val;
    }
  }
  const firstContent = personality.split("\n").find((l) => l.trim() && !l.trim().startsWith("#"));
  return firstContent?.trim().slice(0, 30) ?? "";
}

export function EntityPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const { data: entities = [] } = useQuery({
    queryKey: ["entities"],
    queryFn: () => memoryApi.entities.list().then((r) => r.data),
  });
  const { data: aliases = [] } = useQuery({
    queryKey: ["entityAliases"],
    queryFn: () => memoryApi.entities.aliases().then((r) => r.data),
  });
  const [selected, setSelected] = useState<{ type: string; id: string } | null>(null);
  const [linkInput, setLinkInput] = useState("");

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["entities"] });
    queryClient.invalidateQueries({ queryKey: ["entityAliases"] });
  };

  const linkMutation = useMutation({
    mutationFn: ({ srcType, srcId, tgtType, tgtId }: { srcType: string; srcId: string; tgtType: string; tgtId: string }) =>
      memoryApi.entities.link(srcType, srcId, tgtType, tgtId),
    onSuccess: () => { invalidateAll(); setLinkInput(""); },
  });
  const unlinkMutation = useMutation({
    mutationFn: ({ type, id }: { type: string; id: string }) => memoryApi.entities.unlink(type, id),
    onSuccess: invalidateAll,
  });

  const aliasMap = new Map<string, string>();
  const primaryAliasMap = new Map<string, string[]>();
  for (const a of aliases) {
    const src = `${a.scope_type}:${a.scope_id}`;
    const dst = `${a.primary_scope_type}:${a.primary_scope_id}`;
    aliasMap.set(src, dst);
    primaryAliasMap.set(dst, [...(primaryAliasMap.get(dst) ?? []), src]);
  }

  const selectedEntity = selected
    ? entities.find(
        (e) => e.scope_type === selected.type && e.scope_id === selected.id,
      )
    : null;
  const selectedKey = selected ? `${selected.type}:${selected.id}` : "";
  const linkedTo = aliasMap.get(selectedKey);
  const myAliases = primaryAliasMap.get(selectedKey);

  const handleSelect = (type: string, id: string) => {
    setSelected({ type, id });
  };

  const handleLink = () => {
    if (!selected || !linkInput.includes(":")) return;
    const [srcType, ...rest] = linkInput.split(":");
    const srcId = rest.join(":");
    if (!srcType || !srcId) return;
    linkMutation.mutate({ srcType, srcId, tgtType: selected.type, tgtId: selected.id });
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
      <Card className="min-w-0" title={t("entityList")} subtitle={t("nEntities", { count: entities.length })}>
        <div className="space-y-1 max-h-[28rem] overflow-y-auto">
          {entities.length === 0 && (
            <p className="text-sm text-muted">{t("noEntityProfile")}</p>
          )}
          {entities.map((e) => {
            const key = `${e.scope_type}:${e.scope_id}`;
            const isActive = selected?.type === e.scope_type && selected?.id === e.scope_id;
            const summary = extractEntitySummary(e.personality);
            const hasAlias = aliasMap.has(key) || primaryAliasMap.has(key);
            return (
              <button
                key={key}
                onClick={() => handleSelect(String(e.scope_type ?? ""), String(e.scope_id ?? ""))}
                className={cn(
                  "w-full text-left p-2 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-accent-subtle text-accent"
                    : "text-foreground hover:bg-hover",
                )}
              >
                <div className="flex items-center gap-1.5">
                  {hasAlias && <Link2 size={12} className="flex-shrink-0 text-accent" />}
                  <span className="font-medium truncate">{String(e.scope_id)}</span>
                  <span className="text-xs text-muted">({String(e.scope_type)})</span>
                </div>
                {summary && (
                  <p className="text-xs text-muted mt-0.5 truncate pl-0.5">{summary}</p>
                )}
              </button>
            );
          })}
        </div>
      </Card>

      <Card
        title={selectedEntity ? `${selectedEntity.scope_type}:${selectedEntity.scope_id}` : t("entityProfile")}
        className="min-w-0 break-words md:col-span-2"
      >
        {!selectedEntity ? (
          <p className="text-sm text-muted">{t("selectEntity")}</p>
        ) : (
          <div className="space-y-4">
            <div className="flex flex-wrap gap-3 text-xs text-muted">
              <span>{t("convCount")}: {String(selectedEntity.conv_num ?? 0)}</span>
            </div>

            {(linkedTo || myAliases) && (
              <div className="p-3 rounded-md bg-elevated border border-border space-y-2">
                {linkedTo && (
                  <div className="flex items-center justify-between text-sm">
                    <span className="text-muted">
                      <Link2 size={12} className="inline mr-1" />
                      {t("entityLinkedTo")}: <span className="text-heading">{linkedTo}</span>
                    </span>
                    <button
                      onClick={() => unlinkMutation.mutate({ type: selected!.type, id: selected!.id })}
                      className="flex items-center gap-1 px-2 py-0.5 text-xs rounded-md border border-border text-muted hover:text-danger hover:border-danger transition-colors"
                    >
                      <Unlink size={11} /> {t("unlinkEntity")}
                    </button>
                  </div>
                )}
                {myAliases && myAliases.length > 0 && (
                  <div className="text-sm">
                    <span className="text-muted">{t("entityAliases")}:</span>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {myAliases.map((alias) => {
                        const [aType = "", ...aRest] = alias.split(":");
                        const aId = aRest.join(":");
                        return (
                          <span
                            key={alias}
                            className="inline-flex items-center gap-1 px-2 py-0.5 text-xs rounded-full bg-accent-subtle text-accent"
                          >
                            <Link2 size={10} /> {alias}
                            <button
                              onClick={() => unlinkMutation.mutate({ type: aType, id: aId })}
                              className="ml-0.5 hover:text-danger transition-colors"
                              title={t("unlinkEntity")}
                            >
                              <X size={10} />
                            </button>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {!linkedTo && (
              <div className="flex gap-2">
                <input
                  value={linkInput}
                  onChange={(ev) => setLinkInput(ev.target.value)}
                  placeholder={t("linkPlaceholder")}
                  className="min-w-0 flex-1 bg-card border border-input rounded-md px-3 py-1.5 text-sm text-foreground outline-none focus:border-ring"
                  onKeyDown={(ev) => { if (ev.key === "Enter") handleLink(); }}
                />
                <button
                  onClick={handleLink}
                  disabled={!linkInput.includes(":")}
                  className="flex items-center gap-1 px-3 py-1.5 text-xs font-medium rounded-md border border-border text-foreground hover:bg-hover transition-colors disabled:opacity-40"
                >
                  <Link2 size={12} /> {t("linkEntity")}
                </button>
              </div>
            )}

            <ProfileEditor key={selectedKey} entity={selectedEntity} onDeleted={() => setSelected(null)} />
          </div>
        )}
      </Card>
      <div className="min-w-0 md:col-span-3">
        <ProfileTimeline scopeType={selected?.type} scopeId={selected?.id} />
      </div>
    </div>
  );
}
