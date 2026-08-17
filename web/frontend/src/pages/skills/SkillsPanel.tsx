import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { skillsApi, type SkillItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button, EmptyState, Input, Textarea } from "@/components/ui";
import { Plus, Trash2, Pin, PinOff, Archive, ArchiveRestore, Save, X, GraduationCap, GitMerge, Activity, Boxes, CircleDashed, RefreshCw, Zap } from "lucide-react";

/** 向量构建卡片：状态机可视化 + 手动重建入口（模型切换后的标准操作）。 */
function VectorBuildCard() {
  const { t } = useTranslation(["skills", "common"]);
  const queryClient = useQueryClient();
  const { data: health } = useQuery({
    queryKey: ["skills", "health"],
    queryFn: () => skillsApi.health().then((r) => r.data),
    staleTime: 5_000,
    refetchInterval: (query) => {
      const state = query.state.data?.build?.state;
      return state === "rebuilding" ? 2_000 : false;
    },
  });
  const rebuildMutation = useMutation({
    mutationFn: () => skillsApi.rebuildVectors(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["skills", "health"] }),
  });

  if (!health?.build && !health?.embedding) return null;
  const build = health.build;
  const emb = health.embedding;
  const state = build?.state || (emb?.rebuilding ? "rebuilding" : "idle");
  const progress = build?.progress || { done: emb?.embedded || 0, total: emb?.total || 0 };
  const model = build?.model || emb?.model || "";
  const lastRebuild = build?.last_rebuild;
  const pct = progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;

  return (
    <div className="px-4 py-3 rounded-md border border-border bg-card space-y-2">
      <div className="flex items-center gap-3 flex-wrap text-xs">
        <Boxes size={14} className={state === "rebuilding" ? "text-warn" : "text-ok"} />
        <span className="font-medium text-foreground">{t("vectorBuildTitle")}</span>
        <span className="text-muted">{t("vectorBuildModel")}: {model || t("embeddingNoModel")}</span>
        <span className={cn("text-muted", state === "rebuilding" && "text-warn")}>
          {state === "rebuilding"
            ? t("vectorBuildRebuilding", { done: progress.done, total: progress.total })
            : state === "warming"
              ? t("vectorBuildWarming")
              : t("vectorBuildIdle", { embedded: emb?.embedded ?? progress.done, total: emb?.total ?? progress.total })}
        </span>
        {lastRebuild && (
          <span className="text-muted">
            {t("vectorBuildLastRebuild", {
              count: lastRebuild.count,
              model: lastRebuild.model || "?",
              time: new Date(lastRebuild.at * 1000).toLocaleString(),
            })}
          </span>
        )}
        <div className="ml-auto">
          <Button
            variant="secondary" size="sm"
            onClick={() => { if (window.confirm(t("vectorBuildRebuildConfirm"))) rebuildMutation.mutate(); }}
            disabled={state === "rebuilding" || rebuildMutation.isPending}
            loading={rebuildMutation.isPending}
          >
            <RefreshCw size={13} /> {t("vectorBuildRebuild")}
          </Button>
        </div>
      </div>
      {state === "rebuilding" && progress.total > 0 && (
        <div className="h-1.5 rounded-full bg-bg overflow-hidden">
          <div
            className="h-full bg-warn transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      )}
    </div>
  );
}

/** 库健康摘要条：计数水位 + 待治理事实（零参与/高匹配零消费/触发词碰撞）。 */
function HealthStrip() {
  const { t } = useTranslation(["skills", "common"]);
  const { data: health } = useQuery({
    queryKey: ["skills", "health"],
    queryFn: () => skillsApi.health().then((r) => r.data),
    staleTime: 30_000,
  });
  if (!health) return null;
  const { counts, capacity_reference: ref, embedding } = health;
  const over = counts.active > ref;
  const items: string[] = [];
  if (health.zero_engagement.length > 0) {
    items.push(t("healthZeroEngagement", { count: health.zero_engagement.length }));
  }
  if (health.high_match_low_use.length > 0) {
    items.push(t("healthHighMatchLowUse", { count: health.high_match_low_use.length }));
  }
  const collisionCount = Object.keys(health.trigger_collisions).length;
  if (collisionCount > 0) {
    items.push(t("healthCollisions", { count: collisionCount }));
  }
  const parseErrorCount = Object.keys(health.parse_errors || {}).length;
  if (parseErrorCount > 0) {
    items.push(t("healthParseErrors", { count: parseErrorCount }));
  }
  const embeddingIncomplete = embedding && embedding.embedded < embedding.total;
  return (
    <div className="flex items-center gap-3 flex-wrap text-xs text-muted px-4 py-2 rounded-md border border-border bg-card">
      <Activity size={14} className={over ? "text-warn" : "text-ok"} />
      <span>
        {t("healthCapacity", { active: counts.active, stale: counts.stale, ref })}
        {over && <span className="text-warn"> · {t("healthOverCapacity")}</span>}
      </span>
      {embedding && (
        <span className={embedding.rebuilding || embeddingIncomplete ? "text-warn" : ""}>
          {embedding.rebuilding
            ? t("healthRebuilding", { embedded: embedding.embedded, total: embedding.total })
            : t("healthEmbedding", { embedded: embedding.embedded, total: embedding.total, model: embedding.model || t("embeddingNoModel") })}
        </span>
      )}
      {items.length > 0 && <span className="text-warn">· {items.join(" · ")}</span>}
      {items.length === 0 && !over && !embeddingIncomplete && <span>{t("healthClean")}</span>}
    </div>
  );
}

export function SkillsPanel() {
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

  const embedMutation = useMutation({
    mutationFn: (name: string) => skillsApi.embed(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["skills"] });
      queryClient.invalidateQueries({ queryKey: ["skills", "health"] });
    },
  });

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
    <div className="space-y-4">
      <VectorBuildCard />
      <HealthStrip />

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
        <div className="ml-auto">
          <Button variant="primary" size="sm" onClick={() => setCreating(!creating)}>
            <Plus size={16} /> {t("createNew")}
          </Button>
        </div>
      </div>

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
                {s.merged_into && (
                  <span className="flex items-center gap-1 text-xs text-muted shrink-0" title={t("mergedInto", { name: s.merged_into })}>
                    <GitMerge size={13} />
                  </span>
                )}
                {s.embedded !== null && s.embedded !== undefined && (
                  <button
                    type="button"
                    className={cn(
                      "shrink-0 flex items-center gap-1 px-1.5 py-0.5 rounded text-xs transition-colors",
                      s.embedded
                        ? "text-ok hover:bg-bg"
                        : "text-muted hover:text-foreground hover:bg-bg",
                      embedMutation.isPending && "opacity-50 pointer-events-none",
                    )}
                    title={s.embedded ? t("embeddingRegenerate") : t("embeddingGenerate")}
                    onClick={(e) => {
                      e.stopPropagation();
                      embedMutation.mutate(s.name);
                    }}
                  >
                    {s.embedded ? <Boxes size={13} /> : <CircleDashed size={13} />}
                    <span className="hidden sm:inline">
                      {s.embedded ? t("embeddingReady") : t("embeddingPending")}
                    </span>
                    {!s.embedded && <Zap size={11} className="text-warn" />}
                  </button>
                )}
                <div className="min-w-0">
                  <div className="font-medium text-foreground truncate">{s.name}</div>
                  <div className="text-xs text-muted truncate">{s.description}</div>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs shrink-0">
                <span className={stateColor(s.state)}>{stateLabel(s.state)}</span>
                <span className="text-muted hidden sm:inline">{t("useCount")}: {s.use_count}</span>
                <span className="text-muted hidden sm:inline">{t("matchCount")}: {s.match_count}</span>
                <span className="text-muted hidden lg:inline">{t("patchCount")}: {s.patch_count}</span>
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
                  {detail.merged_into && (
                    <span className="text-warn">{t("mergedInto", { name: detail.merged_into })}</span>
                  )}
                </div>
                {detail.rationale && (
                  <div className="text-xs text-muted px-3 py-2 rounded-md bg-bg">
                    {t("rationale")}: {detail.rationale}
                  </div>
                )}

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
    </div>
  );
}
