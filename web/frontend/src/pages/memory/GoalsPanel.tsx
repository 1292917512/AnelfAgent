import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi } from "@/lib/api";
import type { GoalStep } from "@/lib/types";
import { Card } from "@/components/common/Card";
import { cn } from "@/lib/utils";
import { Trash2, RefreshCw, Plus, Pencil, X, Target, CheckCircle2, Circle, Clock, XCircle, Info } from "lucide-react";

interface GoalData {
  goal_id: string;
  title: string;
  description: string;
  status: "active" | "completed" | "cancelled";
  recurring?: boolean;
  steps: GoalStep[];
  due_time?: string;
  created_at: string;
  updated_at: string;
  memory_id?: number;
}

type GoalFilter = "all" | "active" | "completed";

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
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [editDueTime, setEditDueTime] = useState("");
  const [editRecurring, setEditRecurring] = useState(false);

  const { data: goalsResp } = useQuery({
    queryKey: ["goals", filter],
    queryFn: () => memoryApi.goals.list(filter).then((r) => r.data),
    refetchInterval: 5000,
  });
  const goals: GoalData[] = goalsResp?.goals || [];
  const selectedGoal = goals.find((g) => g.goal_id === selectedGoalId) || null;

  const enterEdit = (goal: GoalData) => {
    setEditTitle(goal.title);
    setEditDesc(goal.description);
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
    mutationFn: (data: { goalId: string } & Record<string, unknown>) => {
      const { goalId, ...rest } = data;
      return memoryApi.goals.update(goalId, rest as Parameters<typeof memoryApi.goals.update>[1]);
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["goals"] }); setEditing(false); },
  });
  const updateStepMutation = useMutation({
    mutationFn: ({ goalId, steps }: { goalId: string; steps: GoalData["steps"] }) =>
      memoryApi.goals.update(goalId, { steps: steps.map((s) => ({ step: s.step, status: s.status as "pending" | "in_progress" | "completed" | "skipped", ...(s.note ? { note: s.note } : {}) })) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["goals"] }),
  });

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
    updateStepMutation.mutate({ goalId: goal.goal_id, steps: goal.steps.map((s, i) => i === stepIdx ? { ...s, status: nextStatus } : s) });
  };

  const isDue = (goal: GoalData) => goal.due_time && new Date(goal.due_time) <= new Date();

  const inputCls = "w-full bg-card border border-input rounded-md px-3 py-2 text-sm text-foreground outline-none focus:border-ring";
  const toggleCls = (on: boolean) => cn("relative inline-flex h-5 w-9 items-center rounded-full transition-colors", on ? "bg-accent" : "bg-secondary border border-border");
  const dotCls = (on: boolean) => cn("inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform", on ? "translate-x-[18px]" : "translate-x-[3px]");

  return (
    <div className="flex gap-4" style={{ minHeight: 400 }}>
      {/* Left: goal list */}
      <div className="w-72 flex-shrink-0 space-y-3">
        <div className="flex items-center gap-2">
          <select value={filter} onChange={(e) => setFilter(e.target.value as GoalFilter)}
            className="flex-1 bg-card border border-input rounded-md px-2 py-1.5 text-xs text-foreground outline-none">
            <option value="active">{t("goalStatusActive")}</option>
            <option value="completed">{t("goalStatusCompleted")}</option>
            <option value="all">{t("goalStatusAll")}</option>
          </select>
          <button onClick={() => { setShowCreate(!showCreate); setSelectedGoalId(null); setEditing(false); }}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground hover:bg-[var(--accent-hover)] transition-all">
            <Plus size={13} /> {t("common:create")}
          </button>
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
                  "p-2.5 rounded-md border cursor-pointer transition-all",
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
        {showCreate ? (
          <Card title={t("createGoal")}>
            <div className="space-y-3">
              <input value={newTitle} onChange={(e) => setNewTitle(e.target.value)} placeholder={t("goalTitlePlaceholder")} className={inputCls} />
              <textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} placeholder={t("goalDescPlaceholder")} rows={2} className={inputCls + " resize-y"} />
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 flex-1">
                  <Clock size={14} className="text-muted" />
                  <input type="datetime-local" value={newDueTime} onChange={(e) => setNewDueTime(e.target.value)}
                    className="flex-1 bg-card border border-input rounded-md px-3 py-1.5 text-xs text-foreground outline-none focus:border-ring" />
                  {newDueTime && <button onClick={() => setNewDueTime("")} className="p-1 text-muted hover:text-danger"><X size={14} /></button>}
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => setNewRecurring(!newRecurring)} className={toggleCls(newRecurring)}><span className={dotCls(newRecurring)} /></button>
                  <span className="text-xs text-muted">{t("recurring")}</span>
                  <span title={t("recurringHint")} className="text-muted hover:text-accent cursor-help transition-colors"><Info size={12} /></span>
                </div>
              </div>
              <div className="space-y-1">
                {newSteps.map((step, i) => (
                  <div key={`new-step-${i}`} className="flex gap-2">
                    <span className="text-xs text-muted w-5 text-right mt-1">{i + 1}.</span>
                    <input value={step} onChange={(e) => { const u = [...newSteps]; u[i] = e.target.value; setNewSteps(u); }} placeholder={t("stepPlaceholder")}
                      className="flex-1 bg-card border border-input rounded-md px-2 py-1 text-xs text-foreground outline-none" />
                    <button onClick={() => setNewSteps(newSteps.filter((_, idx) => idx !== i))} className="p-1 text-muted hover:text-danger"><X size={14} /></button>
                  </div>
                ))}
                <button onClick={() => setNewSteps([...newSteps, ""])} className="text-xs text-accent hover:underline ml-7">+ {t("addStep")}</button>
              </div>
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setShowCreate(false)} className="px-3 py-1.5 text-xs text-muted">{t("common:cancel")}</button>
                <button onClick={() => newTitle && createMutation.mutate()} disabled={!newTitle}
                  className="px-4 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground disabled:opacity-50">{t("common:create")}</button>
              </div>
            </div>
          </Card>
        ) : selectedGoal ? (
          <Card title={editing ? undefined : selectedGoal.title} subtitle={editing ? undefined : selectedGoal.description} actions={
            <div className="flex gap-1">
              {!editing && (
                <button onClick={() => enterEdit(selectedGoal)} title={t("common:edit")}
                  className="p-1.5 text-muted hover:text-accent transition-colors"><Pencil size={16} /></button>
              )}
              {selectedGoal.status === "active" && !editing && (
                <button onClick={() => updateMutation.mutate({ goalId: selectedGoal.goal_id, status: "completed" })} title={t("markComplete")}
                  className="p-1.5 text-muted hover:text-ok transition-colors"><CheckCircle2 size={16} /></button>
              )}
              {selectedGoal.status === "completed" && !editing && (
                <button onClick={() => updateMutation.mutate({ goalId: selectedGoal.goal_id, status: "active" })} title={t("markActive")}
                  className="p-1.5 text-muted hover:text-accent transition-colors"><RefreshCw size={16} /></button>
              )}
              {!editing && (
                <button onClick={() => deleteMutation.mutate(selectedGoal.goal_id)}
                  className="p-1.5 text-muted hover:text-danger transition-colors"><Trash2 size={16} /></button>
              )}
            </div>
          }>
            {editing ? (
              <div className="space-y-3">
                <input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} className={inputCls} />
                <textarea value={editDesc} onChange={(e) => setEditDesc(e.target.value)} rows={2} className={inputCls + " resize-y"} />
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-2 flex-1">
                    <Clock size={14} className="text-muted" />
                    <input type="datetime-local" value={editDueTime} onChange={(e) => setEditDueTime(e.target.value)}
                      className="flex-1 bg-card border border-input rounded-md px-3 py-1.5 text-xs text-foreground outline-none focus:border-ring" />
                    {editDueTime && <button onClick={() => setEditDueTime("")} className="p-1 text-muted hover:text-danger"><X size={14} /></button>}
                  </div>
                  <div className="flex items-center gap-2">
                    <button onClick={() => setEditRecurring(!editRecurring)} className={toggleCls(editRecurring)}><span className={dotCls(editRecurring)} /></button>
                    <span className="text-xs text-muted">{t("recurring")}</span>
                    <span title={t("recurringHint")} className="text-muted hover:text-accent cursor-help transition-colors"><Info size={12} /></span>
                  </div>
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <button onClick={() => setEditing(false)} className="px-3 py-1.5 text-xs text-muted">{t("common:cancel")}</button>
                  <button onClick={() => updateMutation.mutate({
                    goalId: selectedGoal.goal_id, title: editTitle, description: editDesc,
                    due_time: editDueTime || null, recurring: editRecurring,
                  })} className="px-4 py-1.5 text-xs font-medium rounded-md bg-accent text-primary-foreground">{t("common:save")}</button>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
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
                      <div key={step.index ?? i} className="flex items-center gap-2.5 p-2 rounded-md bg-elevated border border-border cursor-pointer hover:border-border-strong transition-all"
                        onClick={() => cycleStepStatus(selectedGoal, i)}>
                        {stepStatusIcon(step.status)}
                        <span className={cn("flex-1 text-sm", step.status === "completed" || step.status === "skipped" ? "text-muted line-through" : "text-foreground")}>
                          {step.content || step.step}
                        </span>
                        {step.note && <span className="text-[10px] text-muted">({step.note})</span>}
                      </div>
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
