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

it("keeps measured timing in details and does not infer missing timing or cache usage", async () => {
  const user = userEvent.setup();
  const entry: Extract<ActivityEntry, { kind: "model" }> = { id: "timing", kind: "model", ts: 1, name: "Timing model", status: "done",
    duration_ms: 24000, ttft_ms: 2000, usage: { total_input_tokens: 1000, cache_read_input_tokens: 950, cache_observable: true } };
  const view = (model: typeof entry) => <MemoryRouter><ActivityEntryView entry={model} live={false} current onReveal={() => {}} /></MemoryRouter>;
  const { rerender } = render(view(entry));
  expect(screen.queryByText("Before first output 2s")).toBeNull();
  await user.click(screen.getByRole("button", { name: /View details: Timing model/ }));
  expect(screen.getByText("Before first output 2s")).toBeTruthy();
  expect(screen.getByText("After first output 22s")).toBeTruthy();
  expect(screen.getByText("Cache read 950 tokens · 95%")).toBeTruthy();
  rerender(view({ ...entry, ttft_ms: null, usage: { total_input_tokens: 1000, cache_observable: false } }));
  expect(screen.queryByText("Before first output 2s")).toBeNull();
  expect(screen.queryByText("After first output 22s")).toBeNull();
  expect(screen.queryByText("Cache read 950 tokens · 95%")).toBeNull();
});
