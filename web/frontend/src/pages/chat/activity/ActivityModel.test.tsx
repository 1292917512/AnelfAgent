import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import type { ActivityEntry } from "@/lib/types/activity";
import { ActivityEntryView } from "./ActivityEntryView";
import { highlightCode } from "@/lib/shiki";
import { ActivityContext } from "./ActivityContext";

vi.mock("@/lib/shiki", () => ({ highlightCode: vi.fn(async () => null) }));

it("mounts recalled content only for expanded blocks and preserves live updates", async () => {
  const user = userEvent.setup();
  const entry: Extract<ActivityEntry, { kind: "context" }> = {
    id: "context", kind: "context", ts: 1, status: "done", duration_ms: 30, block_count: 2,
    blocks: [
      { layer: "memory", label: "Memory block", content: "Recalled detail" },
      { layer: "profile", label: "Profile block", content: "Profile detail" },
    ],
  };
  const view = (value: typeof entry) => <MemoryRouter><ActivityContext entry={value} defaultExpanded /></MemoryRouter>;
  const { container, rerender } = render(view(entry));
  expect(screen.queryByText("Recalled detail")).toBeNull();
  expect(screen.queryByText("Profile detail")).toBeNull();
  const summary = container.querySelector("summary")!;
  await user.click(summary);
  expect(await screen.findByText("Recalled detail")).toBeTruthy();
  expect(screen.queryByText("Profile detail")).toBeNull();
  rerender(view({ ...entry, blocks: [{ ...entry.blocks[0]!, content: "Updated recall" }, entry.blocks[1]!] }));
  expect(screen.getByText("Updated recall")).toBeTruthy();
  await user.click(summary);
  expect(screen.queryByText("Updated recall")).toBeNull();
});

it("renders background status compactly and mounts results only when inspected", async () => {
  const user = userEvent.setup();
  const entry: Extract<ActivityEntry, { kind: "background" }> = {
    id: "image-task", kind: "background", task_kind: "vision", ts: Date.now() / 1000,
    description: "Read error from image", status: "running",
  };
  const view = (task: typeof entry) => <MemoryRouter><ActivityEntryView entry={task} current={false} live={false} onReveal={() => {}} /></MemoryRouter>;
  const { rerender } = render(view(entry));
  expect(screen.getByText(entry.description)).toBeTruthy();
  rerender(view({ ...entry, status: "done", duration_ms: 2500, result: "Detailed image result" }));
  expect(screen.queryByText("Detailed image result")).toBeNull();
  await user.click(screen.getByRole("button", { name: /View details:/ }));
  expect(screen.getByText("Detailed image result")).toBeTruthy();
});

it("preserves explicit reasoning expansion when the model completes", async () => {
  const user = userEvent.setup();
  const entry: Extract<ActivityEntry, { kind: "model" }> = { id: "llm", kind: "model", ts: Date.now() / 1000, name: "Test model", status: "running" };
  const thoughts: Extract<ActivityEntry, { kind: "thinking" }>[] = [{ id: "thought", kind: "thinking", ts: entry.ts, content: "Inspecting execution evidence." }];
  const view = (model: typeof entry) => <MemoryRouter><ActivityEntryView entry={model} thoughts={thoughts} current live onReveal={() => {}} /></MemoryRouter>;
  const { rerender } = render(view(entry));
  expect(screen.getByText(thoughts[0]!.content)).toBeTruthy();
  const toggle = screen.getByRole("button", { expanded: true });
  expect(toggle.textContent).toContain("Test model");
  await user.click(toggle);
  await user.click(screen.getByRole("button", { expanded: false }));
  rerender(view({ ...entry, status: "done", duration_ms: 1200 }));
  expect(screen.getByText(thoughts[0]!.content)).toBeTruthy();
  expect(screen.getByText("Thought complete")).toBeTruthy();
  rerender(view({ ...entry, status: "done", duration_ms: 1200 }));
  expect(screen.getByRole("button", { expanded: true })).toBeTruthy();
  rerender(view({ ...entry, status: "interrupted", duration_ms: 1200 }));
  expect(screen.queryByText("Thought complete")).toBeNull();
  expect(screen.getByText("Interrupted")).toBeTruthy();
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

it("defers code highlighting in an open reasoning inspector until the model finishes", async () => {
  const user = userEvent.setup();
  vi.mocked(highlightCode).mockClear();
  const model: Extract<ActivityEntry, { kind: "model" }> = { id: "live", kind: "model", name: "Live model", status: "running", ts: 1 };
  const view = (status: string, count: number) => <MemoryRouter><ActivityEntryView entry={{ ...model, status }}
    thoughts={[{ id: "thought", kind: "thinking", ts: 1, content: `\`\`\`python\nvalue = ${count}\n\`\`\`` }]} current live={status === "running"} onReveal={() => {}} /></MemoryRouter>;
  const { rerender } = render(view("running", 0));
  await user.click(screen.getByRole("button", { name: /View details: Live model/ }));
  for (let index = 1; index <= 20; index++) rerender(view("running", index));
  expect(highlightCode).not.toHaveBeenCalled();
  rerender(view("done", 20));
  await waitFor(() => expect(highlightCode).toHaveBeenCalled());
  expect(screen.getByRole("dialog").textContent).toContain("value = 20");
});
