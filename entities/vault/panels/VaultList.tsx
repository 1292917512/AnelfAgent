import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Clock, Copy, Eye, EyeOff, Globe, Pencil, Plus, Search, Star, Trash2,
} from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { VaultEntry } from "./types";

interface VaultListProps {
  onCreate: () => void;
  onEdit: (entry: VaultEntry) => void;
}

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-heading placeholder:text-muted focus:outline-none focus:border-accent transition-all";

async function copyText(text: string, okMsg: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(okMsg);
  } catch {
    toast.error("复制失败");
  }
}

/** 条目行内 TOTP 徽标：点击获取当前验证码（需解锁）。 */
function TotpBadge({ entryId }: { entryId: string }) {
  const { t } = useTranslation("vault");
  const [code, setCode] = useState("");
  const [remaining, setRemaining] = useState(0);

  const fetchCode = async () => {
    try {
      const { data } = await vaultApi.totp(entryId);
      setCode(data.code);
      setRemaining(data.remaining);
      window.setTimeout(() => setCode(""), data.remaining * 1000);
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.totpFailed")));
    }
  };

  if (code) {
    return (
      <button
        onClick={() => copyText(code, t("messages.copied"))}
        title={t("list.copyTotp")}
        className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-ok/10 text-ok border border-ok/40 font-mono"
      >
        <Clock size={9} /> {code} · {remaining}s
      </button>
    );
  }
  return (
    <button
      onClick={fetchCode}
      title={t("list.showTotp")}
      className="flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded bg-accent-subtle text-accent border border-accent"
    >
      <Clock size={9} /> TOTP
    </button>
  );
}

/** 密码本条目列表：搜索 + 标签过滤 + 收藏过滤 + 行内操作。 */
export function VaultList({ onCreate, onEdit }: VaultListProps) {
  const { t } = useTranslation("vault");
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [activeTag, setActiveTag] = useState("");
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [revealed, setRevealed] = useState<Record<string, string>>({});

  const { data, refetch } = useQuery({
    queryKey: ["vaultEntries", query, activeTag, favoriteOnly],
    queryFn: () =>
      vaultApi
        .list({ query, tag: activeTag, favorite: favoriteOnly, limit: 200 })
        .then((r) => r.data),
  });

  const { data: allTags } = useQuery({
    queryKey: ["vaultTags"],
    queryFn: () => vaultApi.tags().then((r) => r.data),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => vaultApi.remove(id),
    onSuccess: () => {
      toast.success(t("messages.removeSuccess"));
      queryClient.invalidateQueries({ queryKey: ["vaultEntries"] });
      queryClient.invalidateQueries({ queryKey: ["vaultTags"] });
    },
    onError: (err) => toast.error(vaultErrorMessage(err, t("messages.removeFailed"))),
  });

  const favoriteMutation = useMutation({
    mutationFn: (entry: VaultEntry) =>
      vaultApi.update(entry.id, { favorite: !entry.favorite }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["vaultEntries"] }),
  });

  const entries = useMemo(() => data?.entries ?? [], [data]);

  const toggleReveal = async (entry: VaultEntry) => {
    if (revealed[entry.id]) {
      setRevealed((prev) => {
        const next = { ...prev };
        delete next[entry.id];
        return next;
      });
      return;
    }
    try {
      const { data: res } = await vaultApi.reveal(entry.id);
      setRevealed((prev) => ({ ...prev, [entry.id]: res.value }));
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.revealFailed")));
    }
  };

  const handleRemove = (entry: VaultEntry) => {
    if (window.confirm(t("messages.confirmRemove", { title: entry.title }))) {
      removeMutation.mutate(entry.id);
    }
  };

  return (
    <Card
      title={t("list.title")}
      subtitle={`${t("list.total")}: ${data?.count ?? 0}`}
      actions={
        <div className="flex gap-2">
          <button
            onClick={() => refetch()}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            {t("actions.refresh")}
          </button>
          <button
            onClick={onCreate}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 transition-all"
          >
            <Plus size={14} /> {t("actions.add")}
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("list.searchPlaceholder")}
              className={`${inputCls} pl-9`}
            />
          </div>
          <button
            onClick={() => setFavoriteOnly((v) => !v)}
            title={t("list.favoriteOnly")}
            className={`px-3 rounded-md border transition-all ${
              favoriteOnly
                ? "border-accent bg-accent-subtle text-accent"
                : "border-border bg-elevated text-muted hover:bg-hover"
            }`}
          >
            <Star size={15} fill={favoriteOnly ? "currentColor" : "none"} />
          </button>
        </div>

        {(allTags ?? []).length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {(allTags ?? []).map((tag) => (
              <button
                key={tag}
                onClick={() => setActiveTag((cur) => (cur === tag ? "" : tag))}
                className={`text-[11px] px-2 py-0.5 rounded-full border transition-all ${
                  activeTag === tag
                    ? "border-accent bg-accent-subtle text-accent"
                    : "border-border bg-elevated text-muted hover:bg-hover"
                }`}
              >
                {tag}
              </button>
            ))}
          </div>
        )}

        {entries.length === 0 ? (
          <div className="py-10 text-center text-sm text-muted">{t("list.empty")}</div>
        ) : (
          <div className="space-y-2">
            {entries.map((entry) => (
              <div
                key={entry.id}
                className="flex items-center gap-3 p-3 rounded-lg border border-border bg-elevated hover:border-border-strong transition-all"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-heading truncate">
                      {entry.title}
                    </span>
                    {entry.favorite && (
                      <Star size={11} className="text-warn" fill="currentColor" />
                    )}
                    {entry.has_totp && <TotpBadge entryId={entry.id} />}
                    {entry.tags.map((tag) => (
                      <span
                        key={tag}
                        className="text-[10px] px-1.5 py-0.5 rounded bg-hover text-muted border border-border"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="text-xs text-muted truncate mt-0.5 flex items-center gap-1.5">
                    {entry.username && <span>{entry.username}</span>}
                    {entry.url && (
                      <span className="flex items-center gap-0.5 truncate">
                        <Globe size={10} /> {entry.url}
                      </span>
                    )}
                  </div>
                  {revealed[entry.id] && (
                    <div className="text-xs font-mono text-accent truncate mt-1 select-all">
                      {revealed[entry.id]}
                    </div>
                  )}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {entry.has_password && (
                    <>
                      <button
                        onClick={() => toggleReveal(entry)}
                        title={revealed[entry.id] ? t("actions.hide") : t("actions.reveal")}
                        className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                      >
                        {revealed[entry.id] ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                      <button
                        onClick={async () => {
                          try {
                            const value =
                              revealed[entry.id] ??
                              (await vaultApi.reveal(entry.id)).data.value;
                            await copyText(value, t("messages.copied"));
                          } catch (err) {
                            toast.error(vaultErrorMessage(err, t("messages.revealFailed")));
                          }
                        }}
                        title={t("actions.copyPassword")}
                        className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                      >
                        <Copy size={15} />
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => favoriteMutation.mutate(entry)}
                    title={t("actions.toggleFavorite")}
                    className="p-1.5 rounded-md text-muted hover:text-warn hover:bg-hover transition-all"
                  >
                    <Star size={15} fill={entry.favorite ? "currentColor" : "none"} />
                  </button>
                  <button
                    onClick={() => onEdit(entry)}
                    title={t("actions.edit")}
                    className="p-1.5 rounded-md text-muted hover:text-accent hover:bg-hover transition-all"
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    onClick={() => handleRemove(entry)}
                    title={t("actions.remove")}
                    className="p-1.5 rounded-md text-muted hover:text-danger hover:bg-hover transition-all"
                  >
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}
