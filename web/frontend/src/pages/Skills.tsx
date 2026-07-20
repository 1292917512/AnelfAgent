import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { skillsApi, type SkillItem } from "@/lib/api";
import { PageContainer, PageHeader } from "@/components/common/PageContainer";
import { cn } from "@/lib/utils";
import { Button, EmptyState, Input, Textarea } from "@/components/ui";
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
      ? "text-ok"
      : state === "stale"
        ? "text-warn"
        : "text-muted";

  return (
    <PageContainer>
      <PageHeader
        icon={<GraduationCap size={22} />}
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <Button variant="primary" onClick={() => setCreating(!creating)}>
            <Plus size={16} /> {t("createNew")}
          </Button>
        }
      />

      {/* 创建表单 */}
      {creating && (
        <div className="p-4 rounded-md border border-border bg-card space-y-3">
          <Input
            value={newSkill.name}
            onChange={(e) => setNewSkill({ ...newSkill, name: e.target.value })}
            placeholder={t("newSkillName")}
          />
          <Input
            value={newSkill.description}
            onChange={(e) => setNewSkill({ ...newSkill, description: e.target.value })}
            placeholder={t("description")}
          />
          <Textarea
            value={newSkill.content}
            onChange={(e) => setNewSkill({ ...newSkill, content: e.target.value })}
            placeholder={t("content")}
            rows={5}
            className="font-mono"
          />
          <Input
            value={newSkill.trigger_patterns}
            onChange={(e) => setNewSkill({ ...newSkill, trigger_patterns: e.target.value })}
            placeholder={t("triggerPatterns")}
          />
          <div className="flex gap-2">
            <Button
              variant="primary"
              onClick={() => createMutation.mutate()}
              disabled={!newSkill.name}
              loading={createMutation.isPending}
            >
              {t("save")}
            </Button>
            <Button variant="secondary" onClick={() => setCreating(false)}>
              {t("cancel")}
            </Button>
          </div>
        </div>
      )}

      {/* 工具栏 */}
      <div className="flex items-center gap-3 flex-wrap">
        <Input
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder={t("searchPlaceholder")}
          className="flex-1 min-w-40 max-w-xs"
        />
        <label className="flex items-center gap-1.5 text-sm text-muted cursor-pointer">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
          {t("showArchived")}
        </label>
      </div>

      {/* 技能列表 */}
      {filtered.length === 0 && (
        <EmptyState icon={GraduationCap} title={t("empty")} />
      )}
      <div className="grid gap-3">
        {filtered.map((s) => (
          <div
            key={s.name}
            className={cn(
              "p-4 rounded-md border cursor-pointer transition-all",
              "bg-card hover:border-border-strong",
              s.name === selected
                ? "border-accent shadow-[0_0_0_2px_var(--bg),0_0_0_4px_var(--ring)]"
                : "border-border",
            )}
            onClick={() => { setSelected(s.name === selected ? null : s.name); setEditing(null); }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-3 min-w-0">
                {s.pinned && <Pin size={14} className="text-warn shrink-0" />}
                <div className="min-w-0">
                  <div className="font-medium text-foreground truncate">{s.name}</div>
                  <div className="text-xs text-muted truncate">{s.description}</div>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs shrink-0">
                <span className={stateColor(s.state)}>{stateLabel(s.state)}</span>
                <span className="text-muted hidden sm:inline">{t("useCount")}: {s.use_count}</span>
                <span className="text-muted hidden sm:inline">{t("patchCount")}: {s.patch_count}</span>
              </div>
            </div>

            {/* 详情面板 */}
            {s.name === selected && detail && (
              <div className="mt-4 pt-4 border-t border-border space-y-3" onClick={(e) => e.stopPropagation()}>
                <div className="flex flex-wrap gap-2 text-xs text-muted">
                  <span>{t("createdBy")}: {detail.created_by === "agent" ? t("createdByAgent") : t("createdByUser")}</span>
                  {detail.trigger_patterns.length > 0 && (
                    <span>{t("triggerPatterns")}: {detail.trigger_patterns.join(", ")}</span>
                  )}
                </div>

                {editing ? (
                  <div className="space-y-2">
                    <Input
                      value={editing.description}
                      onChange={(e) => setEditing({ ...editing, description: e.target.value })}
                    />
                    <Textarea
                      value={editing.content}
                      onChange={(e) => setEditing({ ...editing, content: e.target.value })}
                      rows={10}
                      className="font-mono"
                    />
                    <div className="flex gap-2">
                      <Button
                        variant="primary" size="sm"
                        onClick={() => saveMutation.mutate({ name: s.name, data: editing })}
                        loading={saveMutation.isPending}
                      >
                        <Save size={14} /> {t("save")}
                      </Button>
                      <Button variant="secondary" size="sm" onClick={() => setEditing(null)}>
                        <X size={14} /> {t("cancel")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <>
                    <pre className="p-3 rounded-md bg-bg text-sm text-foreground whitespace-pre-wrap font-mono max-h-96 overflow-auto">
                      {detail.content}
                    </pre>
                    <div className="flex flex-wrap gap-2">
                      <Button variant="secondary" size="sm"
                        onClick={() => setEditing({ description: detail.description, content: detail.content || "" })}>
                        {t("edit")}
                      </Button>
                      <Button variant="secondary" size="sm"
                        onClick={() => pinMutation.mutate({ name: s.name, pinned: !s.pinned })}>
                        {s.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                        {s.pinned ? t("unpin") : t("pin")}
                      </Button>
                      {s.state === "archived" ? (
                        <Button variant="secondary" size="sm"
                          onClick={() => stateMutation.mutate({ name: s.name, state: "active" })}>
                          <ArchiveRestore size={14} /> {t("unarchive")}
                        </Button>
                      ) : (
                        <Button variant="secondary" size="sm"
                          onClick={() => stateMutation.mutate({ name: s.name, state: "archived" })}>
                          <Archive size={14} /> {t("archive")}
                        </Button>
                      )}
                      <Button variant="secondary" size="sm"
                        className="border-danger text-danger hover:bg-danger hover:text-white"
                        onClick={() => { if (confirm(t("deleteConfirm"))) deleteMutation.mutate(s.name); }}>
                        <Trash2 size={14} /> {t("delete")}
                      </Button>
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
