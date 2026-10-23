import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AxiosHeaders } from "axios";
import i18n from "@/i18n";
import { uiApi } from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench-store";
import { UiCommandHost } from "./UiCommandHost";

const response = (status: "ok" | "expired") => ({ data: { status }, status: 200, statusText: "OK", headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() } });
afterEach(() => { cleanup(); useWorkbenchStore.setState({ asks: [] }); vi.restoreAllMocks(); });

async function mount() {
  await i18n.changeLanguage("en");
  useWorkbenchStore.setState({ asks: [{ ask_id: "one", question: "Which scope?", options: ["This file"], ts: 1 }] });
  render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><UiCommandHost /></QueryClientProvider></MemoryRouter>);
}

it("allows custom answers alongside choices and preserves a failed submission", async () => {
  const submit = vi.spyOn(uiApi, "answer").mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(response("ok"));
  await mount();
  const input = screen.getByRole("textbox", { name: "Type your answer…" });
  await userEvent.type(input, "All selected files");
  await userEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByRole("alert");
  expect((input as HTMLInputElement).value).toBe("All selected files");
  expect(useWorkbenchStore.getState().asks).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Submit" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(submit).toHaveBeenLastCalledWith("one", "All selected files");
});

it("does not claim delivery for an expired question", async () => {
  vi.spyOn(uiApi, "answer").mockResolvedValue(response("expired"));
  await mount();
  await userEvent.click(screen.getByRole("radio", { name: "This file" }));
  expect(uiApi.answer).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Submit" }));
  await screen.findByText("This question has expired. Your answer was not delivered.");
  expect(useWorkbenchStore.getState().asks).toHaveLength(1);
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(useWorkbenchStore.getState().asks).toHaveLength(0));
});

it("closes a remotely resolved question before showing the next one with a fresh draft", async () => {
  await mount();
  await userEvent.type(screen.getByRole("textbox"), "Unsent draft");
  act(() => {
    useWorkbenchStore.getState().pushAsk({ ask_id: "two", question: "Next question", options: [], ts: 2 });
    useWorkbenchStore.getState().resolveAsk("one");
  });
  await screen.findByText("Next question");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
});

it("opens requested workbench panels from other pages without navigating on unrelated updates", async () => {
  function Location() { return <output data-testid="location">{useLocation().pathname}</output>; }
  render(<MemoryRouter initialEntries={["/context"]}><UiCommandHost /><Location /></MemoryRouter>);
  act(() => useWorkbenchStore.getState().setDraft("Draft stays available"));
  expect(screen.getByTestId("location").textContent).toBe("/context");
  act(() => useWorkbenchStore.getState().openPanel("context"));
  await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/"));
  expect(useWorkbenchStore.getState()).toMatchObject({ dockOpen: true, activeTab: "context" });
});
