import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { skillsApi, type SkillItem } from "@/lib/api";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { cn } from "@/lib/utils";
import { Plus, Trash2, Pin, PinOff, Archive, ArchiveRestore, Save, X, GraduationCap } from "lucide-react";

export default function Skills() {
  const { t } = useTranslation(["skills", "common"]);
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ description: string; content: string } | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [creating, setCreating] = useState(false);
  const [newSkill, setNewSkill] = useState({ name: "", description: "", content: "", trigger_patterns: "" });

  const { data: skills = [] } = useQuery<SkillItem[]>({
    queryKey: ["skills", showArchived],
    queryFn: () => skillsApi.list(showArchived).then((r) => r.data),
  });

  const { data: detail } = useQuery({
    queryKey: ["skill", selected],
    queryFn: () => selected ? skillsApi.get(selected).then((r) => r.data) : null,
    enabled: !!selected,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["skills"] });
    queryClient.invalidateQueries({ queryKey: ["skill", selected] });
  };

  const createMutation = useMutation({
    mutationFn: () => skillsApi.create({
      name: newSkill.name,
      description: newSkill.description,
      content: newSkill.content,
      trigger_patterns: newSkill.trigger_patterns.split(",").map((s) => s.trim()).filter(Boolean),
    }),
    onSuccess: () => { invalidate(); setCreating(false); setNewSkill({ name: "", description: "", content: "", trigger_patterns: "" }); },
  });

  const saveMutation = useMutation({
    mutationFn: ({ name, data }: { name: string; data: { description: string; content: string } }) =>
      skillsApi.update(name, data),
    onSuccess: () => { invalidate(); setEditing(null); },
  });

  const deleteMutation = useMutation({
    mutationFn: (name: string) => skillsApi.remove(name),
    onSuccess: () => { invalidate(); setSelected(null); },
  });

  const pinMutation = useMutation({
    mutationFn: ({ name, pinned }: { name: string; pinned: boolean }) => skillsApi.setPinned(name, pinned),
    onSuccess: invalidate,
  });

  const stateMutation = useMutation({
    mutationFn: ({ name, state }: { name: string; state: string }) => skillsApi.setState(name, state),
    onSuccess: invalidate,
  });

  const filtered = skills.filter((s) =>
    !keyword || s.name.includes(keyword.toLowerCase()) || s.description.includes(keyword),
  );

  const stateLabel = (state: string) =>
    state === "active" ? t("stateActive") : state === "stale" ? t("stateStale") : t("stateArchived");

  const stateColor = (state: string) =>
    state === "active"
      ? "text-[var(--success)]"
      : state === "stale"
        ? "text-[var(--warn)]"
        : "text-[var(--text-dim)]";

  return (
    <PageContainer>
      <PageHeader
        icon={<GraduationCap size={22} />}
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <button
            onClick={() => setCreating(!creating)}
            className="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-[var(--radius-md)]
              bg-[var(--accent)] text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] transition-all"
          >
            <Plus size={16} /> {t("createNew")}
          </button>
        }
      />

      {/* Create Form */}
      {creating && (
        <div className="p-4 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--card)] space-y-3">
          <input
            value={newSkill.name}
            onChange={(e) => setNewSkill({ ...newSkill, name: e.target.value })}
            placeholder={t("newSkillName")}
            className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
          />
          <input
            value={newSkill.description}
            onChange={(e) => setNewSkill({ ...newSkill, description: e.target.value })}
            placeholder={t("description")}
            className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
          />
          <textarea
            value={newSkill.content}
            onChange={(e) => setNewSkill({ ...newSkill, content: e.target.value })}
            placeholder={t("content")}
            rows={5}
            className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] font-mono"
          />
          <input
            value={newSkill.trigger_patterns}
            onChange={(e) => setNewSkill({ ...newSkill, trigger_patterns: e.target.value })}
            placeholder={t("triggerPatterns")}
            className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
          />
          <div className="flex gap-2">
            <button
              onClick={() => createMutation.mutate()}
              disabled={!newSkill.name || createMutation.isPending}
              className="px-4 py-2 text-sm font-medium rounded-[var(--radius-md)] bg-[var(--accent)]
                text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all"
            >
              {t("save")}
            </button>
            <button
              onClick={() => setCreating(false)}
              className="px-4 py-2 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text-dim)] hover:text-[var(--text)] transition-all"
            >
              {t("cancel")}
            </button>
          </div>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-3">
        <input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="flex-1 max-w-xs bg-[var(--card)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
        />
        <label className="flex items-center gap-1.5 text-sm text-[var(--text-dim)] cursor-pointer">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          {t("showArchived")}
        </label>
      </div>

      {/* Skill List */}
      {filtered.length === 0 && (
        <div className="p-8 text-center text-sm text-[var(--text-dim)]">{t("empty")}</div>
      )}
      <div className="grid gap-3">
        {filtered.map((s) => (
          <div
            key={s.name}
            className={cn(
              "p-4 rounded-[var(--radius-md)] border cursor-pointer transition-all",
              "bg-[var(--card)] hover:border-[var(--border-strong)]",
              s.name === selected
                ? "border-[var(--accent)] shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                : "border-[var(--border)]",
            )}
            onClick={() => { setSelected(s.name === selected ? null : s.name); setEditing(null); }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3 min-w-0">
                {s.pinned && <Pin size={14} className="text-[var(--warn)] shrink-0" />}
                <div className="min-w-0">
                  <div className="font-medium text-[var(--text)] truncate">{s.name}</div>
                  <div className="text-xs text-[var(--text-dim)] truncate">{s.description}</div>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs shrink-0">
                <span className={stateColor(s.state)}>{stateLabel(s.state)}</span>
                <span className="text-[var(--text-dim)]">{t("useCount")}: {s.use_count}</span>
                <span className="text-[var(--text-dim)]">{t("patchCount")}: {s.patch_count}</span>
              </div>
            </div>

            {/* Detail Panel */}
            {s.name === selected && detail && (
              <div className="mt-4 pt-4 border-t border-[var(--border)] space-y-3" onClick={(e) => e.stopPropagation()}>
                <div className="flex flex-wrap gap-2 text-xs text-[var(--text-dim)]">
                  <span>{t("createdBy")}: {detail.created_by === "agent" ? t("createdByAgent") : t("createdByUser")}</span>
                  {detail.trigger_patterns.length > 0 && (
                    <span>{t("triggerPatterns")}: {detail.trigger_patterns.join(", ")}</span>
                  )}
                </div>

                {editing ? (
                  <div className="space-y-2">
                    <input
                      value={editing.description}
                      onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                      className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)]"
                    />
                    <textarea
                      value={editing.content}
                      onChange={(e) => setEditing({ ...editing, content: e.target.value })}
                      rows={10}
                      className="w-full bg-[var(--bg)] border border-[var(--input)] rounded-[var(--radius-md)] px-3 py-2 text-sm text-[var(--text)] outline-none focus:border-[var(--ring)] font-mono"
                    />
                    <div className="flex gap-2">
                      <button
                        onClick={() => saveMutation.mutate({ name: s.name, data: editing })}
                        disabled={saveMutation.isPending}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] bg-[var(--accent)]
                          text-[var(--primary-foreground)] hover:bg-[var(--accent-hover)] disabled:opacity-50 transition-all"
                      >
                        <Save size={14} /> {t("save")}
                      </button>
                      <button
                        onClick={() => setEditing(null)}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text-dim)] hover:text-[var(--text)] transition-all"
                      >
                        <X size={14} /> {t("cancel")}
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <pre className="p-3 rounded-[var(--radius-md)] bg-[var(--bg)] text-sm text-[var(--text)] whitespace-pre-wrap font-mono max-h-96 overflow-auto">
                      {detail.content}
                    </pre>
                    <div className="flex flex-wrap gap-2">
                      <button
                        onClick={() => setEditing({ description: detail.description, content: detail.content || "" })}
                        className="px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text)] hover:border-[var(--border-strong)] transition-all"
                      >
                        {t("edit")}
                      </button>
                      <button
                        onClick={() => pinMutation.mutate({ name: s.name, pinned: !s.pinned })}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text)] hover:border-[var(--border-strong)] transition-all"
                      >
                        {s.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                        {s.pinned ? t("unpin") : t("pin")}
                      </button>
                      {s.state === "archived" ? (
                        <button
                          onClick={() => stateMutation.mutate({ name: s.name, state: "active" })}
                          className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text)] hover:border-[var(--border-strong)] transition-all"
                        >
                          <ArchiveRestore size={14} /> {t("unarchive")}
                        </button>
                      ) : (
                        <button
                          onClick={() => stateMutation.mutate({ name: s.name, state: "archived" })}
                          className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--border)] text-[var(--text)] hover:border-[var(--border-strong)] transition-all"
                        >
                          <Archive size={14} /> {t("archive")}
                        </button>
                      )}
                      <button
                        onClick={() => { if (confirm(t("deleteConfirm"))) deleteMutation.mutate(s.name); }}
                        className="flex items-center gap-1 px-3 py-1.5 text-sm rounded-[var(--radius-md)] border border-[var(--danger)] text-[var(--danger)] hover:bg-[var(--danger)] hover:text-white transition-all"
                      >
                        <Trash2 size={14} /> {t("delete")}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </PageContainer>
  );
}
