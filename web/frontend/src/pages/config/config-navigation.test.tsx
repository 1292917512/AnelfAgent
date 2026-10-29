import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { AxiosHeaders } from "axios";
import { beforeEach, expect, it, vi } from "vitest";
import { configMetaApi } from "@/lib/api";
import { configHref, configItemId } from "@/lib/config-navigation";
import type { ConfigMetaItem } from "@/lib/types";
import { ConfigLink } from "@/components/common/ConfigLink";
import { ExecutionLimitNotice } from "@/components/activity/ExecutionLimitNotice";
import Config from "@/pages/Config";

function item(key: string, description: string, advanced = false): ConfigMetaItem {
  return { key, description, advanced, type: "integer", value: 12, default: 0, editable: true,
    min: 0, max: null, step: null, unit: "", tag: "", options: null, source: "mind",
    value_source: "configured", environment_variable: null };
}
const response = { data: { groups: [
  { group: "mind/core", items: [item("max_tool_iterations", "Maximum tool rounds", true)] },
  { group: "context/window", items: [item("max_conversation_size", "Window size"), item("conversation_raw_keep_percent", "Keep percentage")] },
] }, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };

beforeEach(() => { vi.spyOn(configMetaApi, "list").mockResolvedValue(response); });

function open(initial = "/webui/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  const router = createMemoryRouter([{ path: "/", element: <>
    <ConfigLink config={{ key: "max_tool_iterations" }}>Round limit settings</ConfigLink><Outlet />
  </>, children: [
    { index: true, element: <ExecutionLimitNotice limit={{ kind: "iterations", value: 12, config_keys: ["max_tool_iterations"] }} /> },
    { path: "config", element: <Config /> },
  ] }], { basename: "/webui", initialEntries: [initial] });
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>);
  return { router, client };
}

it("encodes keys and groups once without assuming a deployment base path", () => {
  const target = { key: "module/key +#&", group: "module/group" };
  const url = new URL(configHref(target), "https://example.test");
  expect(url.pathname).toBe("/config");
  expect(url.searchParams.get("key")).toBe(target.key);
  expect(url.searchParams.get("group")).toBe(target.group);
  expect(configHref({ group: "network/proxy" })).toBe("/config?group=network%2Fproxy");
});

it("opens the actual limit setting after loading, expands advanced fields and supports Back", async () => {
  let release = () => {};
  const gate = new Promise<void>((resolve) => { release = resolve; });
  vi.mocked(configMetaApi.list).mockImplementation(async () => { await gate; return response; });
  const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
  const user = userEvent.setup();
  const { router } = open();
  expect(screen.getByRole("alert").textContent).toContain("12-round limit");
  const link = screen.getByRole("link", { name: /Open settings.*max_tool_iterations/ });
  expect(link.getAttribute("href")).toBe("/webui/config?key=max_tool_iterations");
  await user.click(link);
  expect(screen.queryByRole("spinbutton")).toBeNull();
  await act(async () => { release(); });
  expect((await screen.findByRole("spinbutton", { name: "Maximum tool rounds" })).getAttribute("min")).toBe("0");
  await waitFor(() => expect(document.activeElement?.id).toBe(configItemId("max_tool_iterations")));
  expect(document.activeElement?.getAttribute("data-config-highlight")).toBe("true");
  expect(scroll).toHaveBeenCalled();
  await act(async () => { await router.navigate(-1); });
  expect(screen.getByRole("alert").textContent).toContain("12-round limit");
});

it("clears a previous search and refocuses when navigating to the same key again", async () => {
  const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
  const user = userEvent.setup();
  const { router, client } = open("/webui/config?q=does-not-match");
  await screen.findByText("No matching config items");
  await user.click(screen.getByRole("link", { name: "Round limit settings" }));
  await waitFor(() => expect(document.activeElement?.id).toBe(configItemId("max_tool_iterations")));
  expect(router.state.location.search).toBe("?key=max_tool_iterations");
  const previous = scroll.mock.calls.length;
  const input = screen.getByRole("spinbutton", { name: "Maximum tool rounds" });
  input.focus();
  await act(async () => { client.setQueryData(["configMeta"], { groups: response.data.groups.map((group) => ({
    ...group, items: group.items.map((field) => ({ ...field, value: 13 })),
  })) }); });
  expect(document.activeElement).toBe(input);
  expect(scroll.mock.calls.length).toBe(previous);
  await user.click(screen.getByRole("link", { name: "Round limit settings" }));
  await waitFor(() => expect(scroll.mock.calls.length).toBeGreaterThan(previous));
});

it("locates either key in a compound configuration row", async () => {
  open("/webui/config?key=conversation_raw_keep_percent");
  await waitFor(() => expect(document.activeElement?.id).toBe(configItemId("conversation_raw_keep_percent")));
  expect(document.activeElement?.className).toContain("ring-accent");
});

it("reports unregistered targets instead of silently showing an unrelated group", async () => {
  open("/webui/config?key=missing_key");
  expect((await screen.findByRole("alert")).textContent).toContain("missing_key is not registered");
  expect(screen.queryByRole("spinbutton")).toBeNull();
});

it("directs explicitly budgeted tasks to change their call budget", () => {
  render(<ExecutionLimitNotice limit={{ kind: "iterations", value: 5, config_keys: [] }} />);
  expect(screen.getByRole("alert").textContent).toContain("explicit round budget");
  expect(screen.queryByRole("link")).toBeNull();
});
