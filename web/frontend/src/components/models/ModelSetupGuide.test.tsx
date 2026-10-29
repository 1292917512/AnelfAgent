import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { AxiosHeaders } from "axios";
import { beforeEach, expect, it, vi } from "vitest";
import { modelsApi, statusApi } from "@/lib/api";
import type { ModelPriorityItem } from "@/lib/types";
import { ModelSetupGuide } from "./ModelSetupGuide";
import { ModelSelect } from "./ModelSelect";

const reply = <T,>(data: T) => ({ data, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } });
const model = (enabled: boolean): ModelPriorityItem => ({
  id: "chat-model", model: "chat-model", enabled, is_default: true, provider_id: "demo", provider_name: "Demo",
  api_type: "openai", supports_tools: true, supports_vision: false, supports_reasoning: false,
  input_cost: null, output_cost: null, context_window: null,
});

beforeEach(() => {
  vi.spyOn(statusApi, "get").mockResolvedValue(reply({ ready: true }));
  vi.spyOn(modelsApi, "priorities").mockResolvedValue(reply({}));
});

function open(initial = "/webui/", picker = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 10_000, gcTime: 0 } } });
  const router = createMemoryRouter([{ path: "/", element: <>
    <ModelSetupGuide />{picker && <ModelSelect />}<Outlet />
  </>, children: [
    { index: true, element: <p>Workspace</p> },
    { path: "models", element: <p>Model configuration</p> },
  ] }], { basename: "/webui", initialEntries: [initial] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { client, router };
}

const missingModelCases: Record<string, ModelPriorityItem[]>[] = [
  {},
  { embedding: [model(true)] },
  { chat: [model(false)] },
];

it.each(missingModelCases)("guides missing or disabled chat models to the configuration page: %j", async (priorities) => {
  vi.mocked(modelsApi.priorities).mockResolvedValue(reply(priorities));
  const user = userEvent.setup();
  const { router } = open();
  await screen.findByRole("status", { name: "Set up a chat model" });
  const link = screen.getByRole("link", { name: "Configure models" });
  expect(link.getAttribute("href")).toBe("/webui/models");
  await user.click(link);
  expect(router.state.location.pathname).toBe("/webui/models");
  expect(screen.queryByRole("status", { name: "Set up a chat model" })).toBeNull();
  expect(screen.getByText("Model configuration")).toBeTruthy();
});

it("stays hidden when at least one chat model is enabled", async () => {
  vi.mocked(modelsApi.priorities).mockResolvedValue(reply({ chat: [model(false), model(true)] }));
  const { client } = open();
  await waitFor(() => expect(client.getQueryState(["priorities"])?.status).toBe("success"));
  expect(screen.queryByText("Set up a chat model")).toBeNull();
});

it("waits for the runtime and model response before deciding whether setup is needed", async () => {
  vi.mocked(statusApi.get).mockResolvedValue(reply({ ready: false }));
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.mocked(modelsApi.priorities).mockImplementation(async () => { await gate; return reply({}); });
  const { client } = open();
  await waitFor(() => expect(client.getQueryState(["status"])?.status).toBe("success"));
  expect(modelsApi.priorities).not.toHaveBeenCalled();
  expect(screen.queryByText("Set up a chat model")).toBeNull();
  await act(async () => { client.setQueryData(["status"], { ready: true }); });
  await waitFor(() => expect(modelsApi.priorities).toHaveBeenCalledOnce());
  expect(screen.queryByText("Set up a chat model")).toBeNull();
  await act(async () => { release(); });
  await screen.findByText("Set up a chat model");
});

it.each(["status", "priorities"] as const)("does not interpret a failed %s request as missing configuration", async (failed) => {
  if (failed === "status") vi.mocked(statusApi.get).mockRejectedValue(new Error("Offline"));
  else vi.mocked(modelsApi.priorities).mockRejectedValue(new Error("Offline"));
  const { client } = open();
  await waitFor(() => expect(client.getQueryState([failed])?.status).toBe("error"));
  expect(screen.queryByText("Set up a chat model")).toBeNull();
});

it("uses the existing model cache and responds to configuration invalidation", async () => {
  const { client } = open("/webui/", true);
  await screen.findByText("Set up a chat model");
  expect(modelsApi.priorities).toHaveBeenCalledOnce();
  vi.mocked(modelsApi.priorities).mockResolvedValue(reply({ chat: [model(true)] }));
  await act(async () => { await client.invalidateQueries({ queryKey: ["priorities"] }); });
  await waitFor(() => expect(screen.queryByText("Set up a chat model")).toBeNull());
  vi.mocked(modelsApi.priorities).mockResolvedValue(reply({ chat: [model(false)] }));
  await act(async () => { await client.invalidateQueries({ queryKey: ["priorities"] }); });
  await screen.findByText("Set up a chat model");
  vi.mocked(modelsApi.priorities).mockRejectedValue(new Error("Refresh failed"));
  await act(async () => { await client.invalidateQueries({ queryKey: ["priorities"] }); });
  await waitFor(() => expect(screen.queryByText("Set up a chat model")).toBeNull());
});
