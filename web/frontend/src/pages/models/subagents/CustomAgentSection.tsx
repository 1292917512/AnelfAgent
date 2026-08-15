/**
 * CustomAgentSection — 自定义子代理档案的增删改查。
 *
 * 自定义档案（tier 0）经 delegate_task(agent_name=名称) 直指；候选池通常单模型，
 * 亦可是多模型降级链。与内置难度档共用统一注册表与同一套 CRUD 路径。
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Pencil, Plus, Trash2, X } from "lucide-react";
import { subAgentsApi } from "@/lib/api";
import type { SubAgentProfile } from "@/lib/types";
import { ModelSelect } from "@/components/models/ModelSelect";
import { Input } from "@/components/ui";

const EMPTY_FORM = { name: "", model_id: "", description: "" };

/** 新建表单 */
function CreateForm({ onCreated }: { onCreated: () => void }) {
  const { t } = useTranslation("models");
  const [form, setForm] = useState({ ...EMPTY_FORM });
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!form.name.trim() || !form.model_id || submitting) return;
    setSubmitting(true);
    try {
      await subAgentsApi.create({
        name: form.name.trim(),
        model_id: form.model_id,
        description: form.description.trim(),
      });
      setForm({ ...EMPTY_FORM });
      onCreated();
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-3 md:p-4 space-y-2">
      <p className="text-sm font-medium text-heading">{t("subagents.createTitle")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          className="!w-40"
          placeholder={t("subagents.namePlaceholder")}
          value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <ModelSelect
          modelType="chat"
          value={form.model_id}
          onChange={(id) => setForm((f) => ({ ...f, model_id: id }))}
          showDefaultWhenEmpty={false}
          placeholder={t("subagents.selectModel")}
          className="w-48"
        />
        <Input
          className="!w-56"
          placeholder={t("subagents.descPlaceholder")}
          value={form.description}
          onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <button
          disabled={!form.name.trim() || !form.model_id || submitting}
          onClick={submit}
          className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted transition-colors hover:text-accent hover:border-accent disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Plus size={13} />
          {t("subagents.create")}
        </button>
      </div>
      <p className="text-xs text-muted">{t("subagents.nameHint")}</p>
    </div>
  );
}

/** 单条档案行：查看态 + 行内编辑态 */
function AgentRow({ profile, onChanged }: {
  profile: SubAgentProfile;
  onChanged: () => void;
}) {
  const { t } = useTranslation("models");
  const [editing, setEditing] = useState(false);
  const [modelId, setModelId] = useState(profile.models[0] ?? "");
  const [description, setDescription] = useState(profile.description);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  const save = async () => {
    const data: { model_id?: string; description?: string } = {};
    if (modelId && modelId !== profile.models[0]) data.model_id = modelId;
    if (description !== profile.description) data.description = description;
    if (Object.keys(data).length === 0) {
      setEditing(false);
      return;
    }
    setPending(true);
    try {
      await subAgentsApi.update(profile.name, data);
      onChanged();
      setEditing(false);
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    setPending(true);
    try {
      await subAgentsApi.remove(profile.name);
      onChanged();
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 min-w-0">
        <Bot size={14} className="text-accent shrink-0" />
        <span className="text-sm font-medium text-heading font-mono truncate">{profile.name}</span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-accent-subtle text-accent font-mono truncate max-w-48">
          {profile.first_available ?? profile.models.join(", ")}
        </span>
        {!profile.model_enabled ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-danger border border-border shrink-0">
            {profile.model_missing ? t("subagents.modelMissing") : t("subagents.unavailable")}
          </span>
        ) : null}
        <div className="ml-auto flex items-center gap-1 shrink-0">
          <button
            onClick={() => setEditing((v) => !v)}
            className="p-1 text-muted transition-colors hover:text-accent"
            title={t("subagents.edit")}
          >
            <Pencil size={13} />
          </button>
          {confirming ? (
            <span className="flex items-center gap-1">
              <button
                disabled={pending}
                onClick={remove}
                className="px-1.5 py-0.5 rounded text-[11px] text-danger hover:bg-danger/10"
              >
                {t("subagents.confirmDelete")}
              </button>
              <button onClick={() => setConfirming(false)} className="p-1 text-muted hover:text-foreground">
                <X size={12} />
              </button>
            </span>
          ) : (
            <button
              onClick={() => setConfirming(true)}
              className="p-1 text-muted transition-colors hover:text-danger"
              title={t("subagents.delete")}
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
      {profile.description && !editing && (
        <p className="text-xs text-muted pl-6 truncate">{profile.description}</p>
      )}

      {editing && (
        <div className="flex flex-wrap items-center gap-2 pl-6">
          <ModelSelect
            modelType="chat"
            value={modelId}
            onChange={setModelId}
            className="w-48"
          />
          <Input
            className="!w-56"
            placeholder={t("subagents.descPlaceholder")}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && save()}
          />
          <button
            onClick={save}
            disabled={pending}
            className="rounded-md border border-border px-2.5 py-1.5 text-xs text-muted transition-colors hover:text-accent hover:border-accent disabled:opacity-40"
          >
            {t("subagents.save")}
          </button>
        </div>
      )}
    </div>
  );
}

export function CustomAgentSection({ profiles, onChanged }: {
  profiles: SubAgentProfile[];
  onChanged: () => void;
}) {
  const { t } = useTranslation("models");

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <p className="text-sm font-medium text-heading">{t("subagents.customTitle")}</p>
        <p className="text-xs text-muted">{t("subagents.customHint")}</p>
      </div>
      <CreateForm onCreated={onChanged} />
      {profiles.length === 0 ? (
        <p className="text-xs text-muted py-2">{t("subagents.empty")}</p>
      ) : (
        <div className="space-y-2">
          {profiles.map((p) => (
            <AgentRow key={p.name} profile={p} onChanged={onChanged} />
          ))}
        </div>
      )}
    </div>
  );
}
