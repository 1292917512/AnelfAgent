import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Save, X } from "lucide-react";
import { sshApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { toast } from "@/stores/toast-store";
import type { SshConnection } from "@/lib/types";

interface ConnectionFormProps {
  /** 编辑目标；null 表示新增 */
  initial: SshConnection | null;
  onDone: () => void;
}

const inputCls =
  "w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground " +
  "placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent";

export function ConnectionForm({ initial, onDone }: ConnectionFormProps) {
  const { t } = useTranslation("ssh");
  const queryClient = useQueryClient();
  const isEdit = initial !== null;

  const [name, setName] = useState(initial?.name ?? "");
  const [host, setHost] = useState(initial?.host ?? "");
  const [port, setPort] = useState(String(initial?.port ?? 22));
  const [username, setUsername] = useState(initial?.username ?? "");
  const [password, setPassword] = useState("");
  const [keyPath, setKeyPath] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [description, setDescription] = useState(initial?.description ?? "");

  const saveMutation = useMutation({
    mutationFn: async () => {
      const portNum = parseInt(port, 10) || 22;
      if (isEdit && initial) {
        return sshApi.update(initial.name, {
          name: name.trim(),
          host: host.trim(),
          port: portNum,
          username: username.trim(),
          // 密码留空 = 保持不变；后端以 null 表示不更新
          password: password || null,
          key_path: keyPath || null,
          passphrase: passphrase || null,
          description: description.trim(),
        });
      }
      return sshApi.create({
        name: name.trim(),
        host: host.trim(),
        port: portNum,
        username: username.trim(),
        password,
        key_path: keyPath,
        passphrase,
        description: description.trim(),
      });
    },
    onSuccess: () => {
      toast.success(isEdit ? t("messages.updateSuccess") : t("messages.createSuccess"));
      queryClient.invalidateQueries({ queryKey: ["sshConnections"] });
      onDone();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.saveFailed"));
    },
  });

  return (
    <Card
      title={isEdit ? t("form.editTitle") : t("form.createTitle")}
      actions={
        <button
          onClick={onDone}
          className="p-1.5 rounded-md text-muted hover:text-foreground hover:bg-hover transition-all"
        >
          <X size={16} />
        </button>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.name")}</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-web" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.host")}</label>
          <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.1.10" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.port")}</label>
          <input value={port} onChange={(e) => setPort(e.target.value)} type="number" placeholder="22" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.username")}</label>
          <input value={username} onChange={(e) => setUsername(e.target.value)} placeholder="root" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.password")}</label>
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            placeholder={isEdit ? t("fields.passwordKeepHint") : t("fields.passwordPlaceholder")}
            className={inputCls}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.keyPath")}</label>
          <input value={keyPath} onChange={(e) => setKeyPath(e.target.value)} placeholder="~/.ssh/id_rsa" className={inputCls} />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.passphrase")}</label>
          <input
            value={passphrase}
            onChange={(e) => setPassphrase(e.target.value)}
            type="password"
            placeholder={t("fields.passphrasePlaceholder")}
            className={inputCls}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-heading mb-1.5">{t("fields.description")}</label>
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t("fields.descriptionPlaceholder")} className={inputCls} />
        </div>
      </div>
      <p className="text-xs text-muted mt-3">{t("form.authHint")}</p>
      <div className="flex justify-end gap-2 mt-4">
        <button
          onClick={onDone}
          className="px-4 py-2 text-sm rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
        >
          {t("actions.cancel")}
        </button>
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || !name.trim() || !host.trim() || !username.trim()}
          className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-md bg-accent text-white hover:opacity-90 disabled:opacity-50 transition-all"
        >
          <Save size={15} /> {t("actions.save")}
        </button>
      </div>
    </Card>
  );
}
