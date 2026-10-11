import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { AxiosHeaders } from "axios";
import i18n from "@/i18n";
import { tagsApi, toolsApi } from "@/lib/api";
import type { ToolGroup } from "@/lib/types";
import { ToolsListPanel } from "./ToolsListPanel";

const GROUPS: ToolGroup[] = [{
  group: "audio",
  description: "Audio tools",
  all_enabled: false,
  any_enabled: true,
  enabled_count: 1,
  total_count: 2,
  tools: [
    { name: "voice_print", source: "audio", enabled: true, description: "识别声纹", tags: ["audio"] },
    { name: "text_read", source: "audio", enabled: false, description: "朗读文本", tags: ["audio"] },
  ],
}];

const ok = <T,>(data: T) => ({ data, status: 200, statusText: "OK", headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() } });

/** 模拟异步导航迟到落地:把 q 改写为输入过程中更早的旧值。 */
function StaleNavigationProbe() {
  const [, setParams] = useSearchParams();
  return <button onClick={() => setParams((current) => {
    const next = new URLSearchParams(current);
    next.set("q", "中");
    return next;
  }, { replace: true })}>stale-navigation</button>;
}

function ParamsProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url-q">{params.get("q") ?? ""}</output>;
}

async function mount(initialEntry = "/tools") {
  await i18n.changeLanguage("en");
  vi.spyOn(toolsApi, "grouped").mockResolvedValue(ok(GROUPS));
  vi.spyOn(toolsApi, "plugins").mockResolvedValue(ok([]));
  vi.spyOn(tagsApi, "toolTags").mockResolvedValue(ok(["audio"]));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <QueryClientProvider client={client}>
        <ToolsListPanel />
        <StaleNavigationProbe />
        <ParamsProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return screen.getByRole("textbox", { name: "Search tool name, description, tags..." }) as HTMLInputElement;
}

afterEach(() => vi.restoreAllMocks());

it("never reverts the input when a stale URL write lands mid-composition", async () => {
  const input = await mount();
  await userEvent.type(input, "中文");
  expect(input.value).toBe("中文");
  expect(screen.getByTestId("url-q").textContent).toBe("中文");
  await userEvent.click(screen.getByRole("button", { name: "stale-navigation" }));
  expect(screen.getByTestId("url-q").textContent).toBe("中");
  expect(input.value).toBe("中文");
});

it("restores the search text from the URL parameter on mount", async () => {
  const input = await mount("/tools?q=声纹");
  expect(input.value).toBe("声纹");
});

it("clears the search input on filter reset", async () => {
  const input = await mount();
  await userEvent.type(input, "voice");
  await userEvent.click(screen.getByRole("button", { name: "Clear filters" }));
  expect(input.value).toBe("");
  expect(screen.getByTestId("url-q").textContent).toBe("");
});
