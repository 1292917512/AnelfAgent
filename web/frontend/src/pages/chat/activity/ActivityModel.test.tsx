import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import type { ActivityEntry } from "@/lib/types/activity";
import { ActivityEntryView } from "./ActivityEntryView";

it("shows live reasoning, unmounts it at completion, and expands from the model row", async () => {
  const user = userEvent.setup();
  const entry: Extract<ActivityEntry, { kind: "model" }> = { id: "llm", kind: "model", ts: Date.now() / 1000, name: "Test model", status: "running" };
  const thoughts: Extract<ActivityEntry, { kind: "thinking" }>[] = [{ id: "thought", kind: "thinking", ts: entry.ts, content: "Inspecting execution evidence." }];
  const view = (model: typeof entry) => <MemoryRouter><ActivityEntryView entry={model} thoughts={thoughts} current live onReveal={() => {}} /></MemoryRouter>;
  const { rerender } = render(view(entry));
  expect(screen.getByText(thoughts[0]!.content)).toBeTruthy();
  const toggle = screen.getByRole("button", { expanded: true });
  expect(toggle.textContent).toContain("Test model");
  rerender(view({ ...entry, status: "done", duration_ms: 1200 }));
  expect(screen.queryByText(thoughts[0]!.content)).toBeNull();
  await user.click(screen.getByRole("button", { expanded: false }));
  expect(screen.getByText(thoughts[0]!.content)).toBeTruthy();
  rerender(view({ ...entry, status: "done", duration_ms: 1200 }));
  expect(screen.getByRole("button", { expanded: true })).toBeTruthy();
});
