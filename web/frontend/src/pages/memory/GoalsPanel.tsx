import { Input, Textarea } from "@/components/ui";
import { Select } from "@/components/ui/Select";
import { Switch } from "@/components/ui/Switch";
import { Button } from "@/components/ui/Button";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import type { GoalData, GoalStep, GoalUpdate } from "@/lib/types";
import { QueryError } from "@/components/common/AsyncState";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2, RefreshCw, Plus, Pencil, X, Target, CheckCircle2, Circle, Clock, XCircle, Info } from "lucide-react";

type GoalFilter = "all" | "active";

export function GoalsPanel() {
  const { t } = useTranslation("memory");
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<GoalFilter>("active");
  const [selectedGoalId, setSelectedGoalId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newDueTime, setNewDueTime] = useState("");
  const [newRecurring, setNewRecurring] = useState(false);
  const [newSteps, setNewSteps] = useState<string[]>([]);
  const [editVersion, setEditVersion] = useState(0);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editDueTime, setEditDueTime] = useState("");
  const [editRecurring, setEditRecurring] = useState(false);

  const { data: goalsResp, error: listError } = useQuery({
    queryKey: ["goals", filter],
    queryFn: () => memoryApi.goals.list(filter, filter === "all").then((r) => r.data),
    refetchInterval: 5000,
  });
  const goals: GoalData[] = goalsResp?.goals || [];
  const selectedGoal = goals.find((g) => g.goal_id === selectedGoalId) || null;
  const isEditing = editing && !selectedGoal?.archived;

  const enterEdit = (goal: GoalData) => {
    setEditVersion(goal.version);
    setEditTitle(goal.title);
    setEditDesc(goal.description ?? "");
    setEditDueTime(goal.due_time || "");
    setEditRecurring(goal.recurring || false);
    setEditing(true);
  };

  const createMutation = useMutation({
    mutationFn: () => memoryApi.goals.create(newTitle, newDesc, newSteps.filter(Boolean), newDueTime || undefined, newRecurring),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["goals"] });
      setShowCreate(false);
      setNewTitle(""); setNewDesc(""); setNewDueTime(""); setNewRecurring(false); setNewSteps([]);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (goalId: string) => memoryApi.goals.delete(goalId),
    onSuccess: (_data, goalId) => { queryClient.invalidateQueries({ queryKey: ["goals"] }); if (selectedGoalId === goalId) setSelectedGoalId(null); },
  });
  const updateMutation = useMutation({
    mutationFn: (data: { goalId: string } & GoalUpdate) => {
      const { goalId, ...rest } = data;
      return memoryApi.goals.update(goalId, rest);
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["goals"] }); setEditing(false); },
  });
  const updateStepMutation = useMutation({
    mutationFn: ({ goalId, steps, version }: { goalId: string; steps: GoalData["steps"]; version: number }) =>
      memoryApi.goals.update(goalId, { steps, expected_version: version }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["goals"] }),
  });
  const restoreMutation = useMutation({
    mutationFn: (goal: GoalData) => memoryApi.goals.restore(goal.goal_id, goal.version),
    onSuccess: () => { setEditing(false); return queryClient.invalidateQueries({ queryKey: ["goals"] }); },
  });
  const error = listError || createMutation.error || deleteMutation.error || updateMutation.error || updateStepMutation.error || restoreMutation.error;

  const stepStatusIcon = (status: string) => {
    switch (status) {
      case "completed": return <CheckCircle2 size={14} className="text-ok" />;
      case "in_progress": return <Clock size={14} className="text-accent" />;
      case "skipped": return <XCircle size={14} className="text-muted" />;
      default: return <Circle size={14} className="text-muted" />;
    }
  };

  const cycleStepStatus = (goal: GoalData, stepIdx: number) => {
    const order = ["pending", "in_progress", "completed", "skipped"];
    const step = goal.steps[stepIdx];
    if (!step) return;
    const nextStatus = (order[(order.indexOf(step.status) + 1) % order.length] ?? "pending") as GoalStep["status"];
    updateStepMutation.mutate({ goalId: goal.goal_id, version: goal.version, steps: goal.steps.map((s, i) => i === stepIdx ? { ...s, status: nextStatus } : s) });
  };

  const isDue = (goal: GoalData) => goal.due_time && new Date(goal.due_time) <= new Date();


  return (
    <div className="flex flex-col gap-4 md:flex-row" style={{ minHeight: 400 }}>
      {/* Left: goal list */}
      <div className="w-full shrink-0 space-y-3 md:w-72">
        <div className="flex items-center gap-2">
          <Select aria-label={t("common:status")} value={filter} onChange={(e) => setFilter(e.target.value as GoalFilter)}
            className="flex-1">
            <option value="active">{t("goalStatusActive")}</option>
            <option value="all">{t("goalStatusAll")}</option>
          </Select>
          <Button variant="primary" onClick={() => { setShowCreate(!showCreate); setSelectedGoalId(null); setEditing(false); }}>
            <Plus size={13} /> {t("common:create")}
          </Button>
        </div>

        <div className="space-y-1.5 max-h-[500px] overflow-y-auto">
          {goals.length === 0 && <p className="text-xs text-muted p-2">{t("noGoals")}</p>}
          {goals.map((goal) => {
            const done = goal.steps.filter((s) => s.status === "completed").length;
            const due = isDue(goal);
            return (
              <div key={goal.goal_id}
                onClick={() => { setSelectedGoalId(goal.goal_id); setShowCreate(false); setEditing(false); }}
                className={cn(
                  "p-2.5 rounded-md border cursor-pointer transition-[color,background-color,border-color,box-shadow,opacity]",
                  selectedGoalId === goal.goal_id ? "border-accent bg-accent-subtle" : "border-border bg-elevated hover:border-border-strong",
                  goal.status === "completed" && !goal.recurring && "opacity-60",
                )}>
                <div className="flex items-center gap-2 mb-1">
                  <Target size={12} className={cn("flex-shrink-0", due ? "text-danger" : "text-accent")} />
                  <span className="text-xs font-medium text-heading truncate flex-1">{goal.title}</span>
                  {goal.recurring && <span title={t("recurring")}><RefreshCw size={10} className="text-accent flex-shrink-0" /></span>}
                  <span className={cn("text-[9px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0",
                    goal.status === "completed" ? "bg-ok-subtle text-ok"
                      : goal.status === "cancelled" ? "bg-secondary text-muted"
                      : "bg-accent-subtle text-accent"
                  )}>{t(`goalStatus${goal.status.charAt(0).toUpperCase() + goal.status.slice(1)}`)}</span>
                </div>
                <div className="flex items-center gap-2 text-[10px] text-muted">
                  {goal.steps.length > 0 && <span>{done}/{goal.steps.length} {t("stepsUnit")}</span>}
                  {goal.due_time && <span className={cn("flex items-center gap-0.5", due && "text-danger")}><Clock size={10} />{goal.due_time}</span>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Right: detail / edit / create */}
      <div className="flex-1 min-w-0">
        {error && <QueryError error={error} compact />}
        {showCreate ? (
          <Card title={t("createGoal")}>
            <div className="space-y-3">
              <Input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder={t("goalTitlePlaceholder")} className="w-full" />
              <Textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder={t("goalDescPlaceholder")} rows={2} className="w-full" />
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex min-w-0 items-center gap-2 flex-1">
                  <Clock size={14} className="text-muted" />
                  <Input type="datetime-local" value={newDueTime} onChange={(e) => setNewDueTime(e.target.value)}
                    className="flex-1" />
                  {newDueTime && <button onClick={() => setNewDueTime("")} className="p-1 text-muted hover:text-danger"><X size={14} /></button>}
                </div>
                <div className="flex items-center gap-2">
                  <Switch checked={newRecurring} onChange={setNewRecurring} label={t("recurring")} />
                  <span className="text-xs text-muted">{t("recurring")}</span>
                  <span title={t("recurringHint")} className="text-muted hover:text-accent cursor-help transition-colors"><Info size={12} /></span>
                </div>
              </div>
              <div className="space-y-1">
                {newSteps.map((step, i) => (
                  <div key={`new-step-${i}`} className="flex gap-2">
                    <span className="text-xs text-muted w-5 text-right mt-1">{i + 1}.</span>
                    <Input value={step} onChange={(e) => { const u = [...newSteps]; u[i] = e.target.value; setNewSteps(u); }} placeholder={t("stepPlaceholder")}
                      className="flex-1" />
                    <button onClick={() => setNewSteps(newSteps.filter((_, idx) => idx !== i))} className="p-1 text-muted hover:text-danger"><X size={14} /></button>
                  </div>
                ))}
                <button onClick={() => setNewSteps([...newSteps, ""])} className="text-xs text-accent hover:underline ml-7">+ {t("addStep")}</button>
              </div>
              <div className="flex flex-wrap justify-end gap-2 pt-2">
                <Button onClick={() => setShowCreate(false)} disabled={createMutation.isPending}>{t("common:cancel")}</Button>
                <Button variant="primary" onClick={() => newTitle && createMutation.mutate()} disabled={!newTitle} loading={createMutation.isPending}>{t("common:create")}</Button>
              </div>
            </div>
          </Card>
        ) : selectedGoal ? (
          <Card title={isEditing ? undefined : selectedGoal.title} subtitle={isEditing ? undefined : selectedGoal.description} actions={selectedGoal.archived ? (
            <Button loading={restoreMutation.isPending} onClick={() => restoreMutation.mutate(selectedGoal)}>{t("restoreArchivedGoal")}</Button>
          ) : (
            <div className="flex gap-1">
              {!editing && (
                <button onClick={() => enterEdit(selectedGoal)} title={t("common:edit")}
                  className="p-1.5 text-muted hover:text-accent transition-colors"><Pencil size={16} /></button>
              )}
              {selectedGoal.status === "active" && !editing && (
                <button onClick={() => updateMutation.mutate({ goalId: selectedGoal.goal_id, expected_version: selectedGoal.version, status: "completed" })} title={t("markComplete")}
                  className="p-1.5 text-muted hover:text-ok transition-colors"><CheckCircle2 size={16} /></button>
              )}
              {selectedGoal.status !== "active" && !editing && (
                <button onClick={() => updateMutation.mutate({ goalId: selectedGoal.goal_id, expected_version: selectedGoal.version, status: "active" })} title={t("markActive")}
                  className="p-1.5 text-muted hover:text-accent transition-colors"><RefreshCw size={16} /></button>
              )}
              {!editing && (
                <button onClick={() => deleteMutation.mutate(selectedGoal.goal_id)}
                  className="p-1.5 text-muted hover:text-danger transition-colors"><Trash2 size={16} /></button>
              )}
            </div>
          )}>
            {isEditing ? (
              <div className="space-y-3">
                <Input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} className="w-full" />
                <Textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={2} className="w-full" />
                <div className="flex flex-wrap items-center gap-3">
                  <div className="flex min-w-0 items-center gap-2 flex-1">
                    <Clock size={14} className="text-muted" />
                    <Input type="datetime-local" value={editDueTime} onChange={(e) => setEditDueTime(e.target.value)}
                      className="flex-1" />
                    {editDueTime && <button onClick={() => setEditDueTime("")} className="p-1 text-muted hover:text-danger"><X size={14} /></button>}
                  </div>
                  <div className="flex items-center gap-2">
                    <Switch checked={editRecurring} onChange={setEditRecurring} label={t("recurring")} />
                    <span className="text-xs text-muted">{t("recurring")}</span>
                    <span title={t("recurringHint")} className="text-muted hover:text-accent cursor-help transition-colors"><Info size={12} /></span>
                  </div>
                </div>
                <div className="flex flex-wrap justify-end gap-2 pt-2">
                  <Button onClick={() => setEditing(false)} disabled={updateMutation.isPending}>{t("common:cancel")}</Button>
                  <Button variant="primary" loading={updateMutation.isPending} onClick={() => updateMutation.mutate({
                    goalId: selectedGoal.goal_id, expected_version: editVersion, title: editTitle, description: editDesc,
                    due_time: editDueTime || null, recurring: editRecurring,
                  })}>{t("common:save")}</Button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                {selectedGoal.archived && <p className="text-xs text-muted">{t("goalArchived", {
                  reason: t(`goalArchiveReason.${selectedGoal.archive_reason}`, { defaultValue: selectedGoal.archive_reason || t("common:noData") }),
                })}</p>}
                <div className="flex flex-wrap gap-3 text-xs text-muted">
                  <span className={cn("px-2 py-0.5 rounded-full font-medium",
                    selectedGoal.status === "completed" ? "bg-ok-subtle text-ok"
                      : selectedGoal.status === "cancelled" ? "bg-secondary text-muted"
                      : "bg-accent-subtle text-accent"
                  )}>{t(`goalStatus${selectedGoal.status.charAt(0).toUpperCase() + selectedGoal.status.slice(1)}`)}</span>
                  {selectedGoal.recurring && <span className="px-2 py-0.5 rounded-full font-medium bg-accent-subtle text-accent flex items-center gap-1"><RefreshCw size={10} />{t("recurring")}</span>}
                  <span>{t("goalCreated")}: {new Date(selectedGoal.created_at).toLocaleString()}</span>
                  {selectedGoal.updated_at !== selectedGoal.created_at && <span>{t("goalUpdated")}: {new Date(selectedGoal.updated_at).toLocaleString()}</span>}
                  {selectedGoal.due_time && <span className={cn("flex items-center gap-1", isDue(selectedGoal) && "text-danger font-medium")}>
                    <Clock size={12} />{t("dueTime")}: {selectedGoal.due_time}
                  </span>}
                </div>
                {selectedGoal.steps.length > 0 ? (
                  <div className="space-y-1.5">
                    <span className="text-xs font-medium text-heading">{t("stepsLabel")} ({selectedGoal.steps.filter(s => s.status === "completed").length}/{selectedGoal.steps.length})</span>
                    {selectedGoal.steps.map((step, i) => (
                      <button type="button" key={step.step_id ?? i} disabled={selectedGoal.archived || updateStepMutation.isPending}
                        className="flex w-full items-center gap-2.5 p-2 text-left rounded-md bg-elevated border border-border enabled:hover:border-border-strong"
                        onClick={() => cycleStepStatus(selectedGoal, i)}>
                        {stepStatusIcon(step.status)}
                        <span className={cn("flex-1 text-sm", step.status === "completed" || step.status === "skipped" ? "text-muted line-through" : "text-foreground")}>
                          {step.content || step.step}
                        </span>
                        {step.note && <span className="text-[10px] text-muted">({step.note})</span>}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted">{t("noSteps")}</p>
                )}
              </div>
            )}
          </Card>
        ) : (
          <div className="flex items-center justify-center h-full text-sm text-muted">{t("selectGoalHint")}</div>
        )}
      </div>
    </div>
  );
}
