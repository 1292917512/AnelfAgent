import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { AxiosHeaders } from "axios";
import i18n from "@/i18n";
import { memoryApi } from "@/lib/api";
import type { GoalData } from "@/lib/types";
import { GoalsPanel } from "./GoalsPanel";

it("preserves step content, identity and notes while submitting the displayed version", async () => {
  await i18n.changeLanguage("zh");
  const goal: GoalData = {
    goal_id: "test-goal", title: "回归目标", description: "", status: "active", version: 3,
    created_at: "2026-01-01", updated_at: "2026-01-01",
    steps: [{ step_id: "stable-step", index: 0, content: "检查素材", note: "保留原备注", status: "pending" }],
  };
  const response = { status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };
  const list = vi.spyOn(memoryApi.goals, "list").mockResolvedValue({ ...response, data: { goals: [goal] } });
  const update = vi.spyOn(memoryApi.goals, "update").mockResolvedValue({ ...response, data: goal });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  const view = render(<QueryClientProvider client={client}><GoalsPanel /></QueryClientProvider>);
  try {
    await user.click(await screen.findByText("回归目标"));
    await user.click(screen.getByText("检查素材"));
    await waitFor(() => expect(update).toHaveBeenCalledWith("test-goal", {
      expected_version: 3, steps: [{ ...goal.steps[0], status: "in_progress" }],
    }));
  } finally {
    view.unmount();
    client.clear();
    list.mockRestore();
    update.mockRestore();
  }
});

it("refreshes a stale delete and waits for another decision on the new version", async () => {
  await i18n.changeLanguage("zh");
  const goal: GoalData = {
    goal_id: "review", title: "初稿", status: "active", version: 3,
    created_at: "2026-01-01", updated_at: "2026-01-01", steps: [],
  };
  const response = { status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };
  const list = vi.spyOn(memoryApi.goals, "list")
    .mockResolvedValueOnce({ ...response, data: { goals: [goal] } })
    .mockResolvedValue({ ...response, data: { goals: [{ ...goal, title: "新进展", version: 4 }] } });
  const remove = vi.spyOn(memoryApi.goals, "delete")
    .mockRejectedValueOnce(new Error("目标已变化"))
    .mockResolvedValue({ ...response, data: { ok: true } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  const view = render(<QueryClientProvider client={client}><GoalsPanel /></QueryClientProvider>);
  try {
    await user.click(await screen.findByText(goal.title));
    await user.click(screen.getByRole("button", { name: "删除" }));
    await screen.findAllByText("新进展");
    expect(remove.mock.calls).toEqual([["review", 3]]);
    await user.click(screen.getByRole("button", { name: "删除" }));
    await waitFor(() => expect(remove.mock.calls).toEqual([["review", 3], ["review", 4]]));
  } finally {
    view.unmount();
    client.clear();
    list.mockRestore();
    remove.mockRestore();
  }
});

it("shows archived goals under All and restores the viewed version with its steps read-only", async () => {
  await i18n.changeLanguage("zh");
  const goal: GoalData = {
    goal_id: "archived", title: "已归档目标", status: "completed", version: 7,
    archived: true, archive_reason: "all_steps_completed", created_at: "2026-01-01", updated_at: "2026-01-02",
    steps: [{ step_id: "check", content: "核实资料", status: "completed", note: "已核验" }],
  };
  const response = { status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };
  const list = vi.spyOn(memoryApi.goals, "list").mockImplementation(async (_status, includeArchived) =>
    ({ ...response, data: { goals: includeArchived ? [goal] : [] } }));
  const restore = vi.spyOn(memoryApi.goals, "restore").mockResolvedValue({ ...response, data: { ok: true } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const user = userEvent.setup();
  const view = render(<QueryClientProvider client={client}><GoalsPanel /></QueryClientProvider>);
  try {
    await user.selectOptions(screen.getByRole("combobox"), "all");
    await user.click(await screen.findByText(goal.title));
    expect(list).toHaveBeenCalledWith("all", true);
    expect(screen.getByText("归档原因：所有步骤已完成")).toBeTruthy();
    expect(screen.getByRole("button", { name: /核实资料/ })).toHaveProperty("disabled", true);
    await user.click(screen.getByRole("button", { name: "取回归档" }));
    await waitFor(() => expect(restore).toHaveBeenCalledWith(goal.goal_id, goal.version));
  } finally {
    view.unmount();
    client.clear();
    list.mockRestore();
    restore.mockRestore();
  }
});
