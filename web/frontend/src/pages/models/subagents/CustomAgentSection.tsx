/**
 * CustomAgentSection — 自定义子代理档案的增删改查。
 *
 * 自定义档案（tier 0）经 delegate_task(agent_name=名称) 直指；候选池通常单模型，
 * 亦可是多模型降级链。档案可携带执行面（facets）：instructions 专职守则 /
 * tool_tags 工具选择器 / blocked_tools 追加屏蔽 / output_schema 结构化产出契约——
 * 只在委托的 reflect 临时上下文生效，编辑即时持久化热生效。
 */
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Pencil, Trash2, X } from "lucide-react";
import { apiErrorMessage, subAgentsApi } from "@/lib/api";
import type { SubAgentProfile } from "@/lib/types";
import { CreateAgentForm } from "./CreateAgentForm";
import { EditAgentForm } from "./EditAgentForm";
import { useProfileWrite } from "./useProfileWrite";

/** 单条档案行：查看态 + 行内编辑态（模型/描述 + 可展开执行面） */
function AgentRow({ profile, onChanged }: {
  profile: SubAgentProfile;
  onChanged: () => void;
}) {
  const { t } = useTranslation("models");
  const [editing, setEditing] = useState(false);
  const editButton = useRef<HTMLButtonElement>(null);
  const [confirming, setConfirming] = useState(false);
  const remove = useProfileWrite(subAgentsApi.remove, onChanged);
  const closeEditor = () => {
    setEditing(false);
    requestAnimationFrame(() => editButton.current?.focus());
  };

  const hasFacets = Boolean(
    profile.instructions || profile.tool_tags.length
    || profile.blocked_tools.length || profile.output_schema,
  );

  return (
    <div className="rounded-md border border-border bg-card p-3 space-y-2">
      <div className="flex items-center gap-2 min-w-0">
        <Bot size={14} className="text-accent shrink-0" />
        <span className="text-sm font-medium text-heading font-mono truncate">{profile.name}</span>
        <span className="text-xs px-2 py-0.5 rounded-full bg-accent-subtle text-accent font-mono truncate max-w-48">
          {profile.first_available ?? profile.models.join(", ")}
        </span>
        {hasFacets && !editing && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-accent border border-border shrink-0">
            {t("subagents.facetsBadge")}
          </span>
        )}
        {!profile.model_enabled ? (
          <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-secondary text-danger border border-border shrink-0">
            {profile.model_missing ? t("subagents.modelMissing") : t("subagents.unavailable")}
          </span>
        ) : null}
        {!editing && <div className="ml-auto flex items-center gap-1 shrink-0">
          <button
            ref={editButton} disabled={confirming} onClick={() => setEditing(true)}
            className="p-1 text-muted transition-colors hover:text-accent"
            title={t("subagents.edit")}
          >
            <Pencil size={13} />
          </button>
          {confirming ? (
            <span className="flex items-center gap-1">
              <button
                disabled={remove.pending}
                onClick={() => remove.submit(profile.name)}
                className="px-1.5 py-0.5 rounded text-[11px] text-danger hover:bg-danger/10"
              >
                {remove.error ? t("common:retry") : t("subagents.confirmDelete")}
              </button>
              <button disabled={remove.pending} aria-label={t("common:cancel")} onClick={() => setConfirming(false)} className="p-1 text-muted hover:text-foreground">
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
        </div>}
      </div>
      {confirming && remove.error && <p role="alert" className="text-sm text-danger">{apiErrorMessage(remove.error, t("common:requestFailed"))}</p>}
      {profile.description && !editing && (
        <p className="text-xs text-muted pl-6 truncate">{profile.description}</p>
      )}

      {editing && <EditAgentForm profile={profile} onCancel={closeEditor} onSaved={() => { closeEditor(); onChanged(); }} />}
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
      <CreateAgentForm names={profiles.map((p) => p.name)} onCreated={onChanged} />
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
