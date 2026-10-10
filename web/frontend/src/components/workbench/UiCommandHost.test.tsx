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
  await userEvent.click(screen.getByRole("button", { name: "This file" }));
  await screen.findByText("This question has expired. Your answer was not delivered.");
  expect(useWorkbenchStore.getState().asks).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(useWorkbenchStore.getState().asks).toHaveLength(0);
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
