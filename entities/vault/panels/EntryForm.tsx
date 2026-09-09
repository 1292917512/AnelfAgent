import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Check, RefreshCw, X } from "lucide-react";
import { vaultApi, vaultErrorMessage } from "./api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { VaultEntry } from "./types";

interface EntryFormProps {
  /** null = 新增；VaultEntry = 编辑 */
  initial: VaultEntry | null;
  onDone: () => void;
}

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-heading placeholder:text-muted focus:outline-none focus:border-accent transition-all";
const labelCls = "block text-xs font-medium text-muted mb-1";

/** 条目新增/编辑表单（编辑时密码/TOTP 留空 = 保持不变）。 */
export function EntryForm({ initial, onDone }: EntryFormProps) {
  const { t } = useTranslation("vault");
  const queryClient = useQueryClient();
  const isEdit = initial !== null;

  const [title, setTitle] = useState(initial?.title ?? "");
  const [username, setUsername] = useState(initial?.username ?? "");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [password, setPassword] = useState("");
  const [totpSecret, setTotpSecret] = useState("");
  const [tags, setTags] = useState((initial?.tags ?? []).join(", "));
  const [favorite, setFavorite] = useState(initial?.favorite ?? false);

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["vaultEntries"] });
    queryClient.invalidateQueries({ queryKey: ["vaultTags"] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const parsedTags = tags
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (isEdit) {
        await vaultApi.update(initial.id, {
          title,
          username,
          url,
          tags: parsedTags,
          favorite,
          ...(password ? { password } : {}),
          ...(totpSecret ? { totp_secret: totpSecret } : {}),
        });
      } else {
        await vaultApi.create({
          title,
          username,
          url,
          password,
          totp_secret: totpSecret,
          notes: "",
          tags: parsedTags,
          favorite,
        });
      }
    },
    onSuccess: () => {
      toast.success(t(isEdit ? "messages.updateSuccess" : "messages.addSuccess"));
      invalidate();
      onDone();
    },
    onError: (err) =>
      toast.error(vaultErrorMessage(err, t(isEdit ? "messages.updateFailed" : "messages.addFailed"))),
  });

  const generatePassword = async () => {
    try {
      const { data } = await vaultApi.generate({
        length: 20,
        symbols: true,
        exclude_ambiguous: false,
        memorable: false,
      });
      setPassword(data.password);
    } catch (err) {
      toast.error(vaultErrorMessage(err, t("messages.generateFailed")));
    }
  };

  return (
    <Card
      title={isEdit ? t("form.editTitle") : t("form.createTitle")}
      actions={
        <div className="flex gap-2">
          <button
            onClick={onDone}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
          >
            <X size={14} /> {t("actions.cancel")}
          </button>
          <button
            onClick={() => saveMutation.mutate()}
            disabled={!title.trim() || saveMutation.isPending}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
          >
            <Check size={14} /> {t("actions.save")}
          </button>
        </div>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className={labelCls}>{t("form.title")} *</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)}
            placeholder={t("form.titlePlaceholder")} className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>{t("form.username")}</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)}
            placeholder={t("form.usernamePlaceholder")} className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>{t("form.url")}</label>
          <input value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com" className={inputCls} />
        </div>
        <div>
          <label className={labelCls}>
            {t("form.password")}
            {isEdit && <span className="ml-1 text-muted/60">{t("form.keepBlank")}</span>}
          </label>
          <div className="flex gap-2">
            <input value={password} onChange={(e) => setPassword(e.target.value)}
              placeholder={isEdit ? "••••••••" : t("form.passwordPlaceholder")}
              className={inputCls} type="text" autoComplete="off" />
            <button
              onClick={generatePassword}
              title={t("actions.generate")}
              className="px-2.5 rounded-md border border-border bg-elevated text-muted hover:text-accent hover:bg-hover transition-all shrink-0"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
        <div>
          <label className={labelCls}>
            {t("form.totp")}
            {isEdit && <span className="ml-1 text-muted/60">{t("form.keepBlank")}</span>}
          </label>
          <input value={totpSecret} onChange={(e) => setTotpSecret(e.target.value)}
            placeholder={isEdit && initial.has_totp ? "••••••••" : t("form.totpPlaceholder")}
            className={inputCls} autoComplete="off" />
        </div>
        <div>
          <label className={labelCls}>{t("form.tags")}</label>
          <input value={tags} onChange={(e) => setTags(e.target.value)}
            placeholder={t("form.tagsPlaceholder")} className={inputCls} />
        </div>
        <label className="flex items-center gap-2 text-sm text-heading md:col-span-2 cursor-pointer">
          <input type="checkbox" checked={favorite}
            onChange={(e) => setFavorite(e.target.checked)}
            className="accent-accent" />
          {t("form.favorite")}
        </label>
      </div>
    </Card>
  );
}
