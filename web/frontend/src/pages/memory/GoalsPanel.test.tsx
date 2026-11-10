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
