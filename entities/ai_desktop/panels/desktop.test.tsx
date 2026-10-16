import { MemoryRouter } from "react-router-dom";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { ConfigField } from "./ConfigField";
import { ModuleCard } from "./ModuleCard";
import { SubscriptionDetailView } from "./SubscriptionDetailView";
import type { DesktopModuleInfo, ModuleConfigItem } from "./types";
import zh from "./locales/zh.json";

const moduleInfo: DesktopModuleInfo = {
  key: "subscription", display_name: "订阅额度", description: "", priority: 30,
  enabled: true, default_enabled: true, refresh_interval: 1800, last_refresh: null,
  last_error: "", configs: [], current_content: null, detail: {},
};
const config: ModuleConfigItem = {
  key: "refresh", name: "refresh", description: "刷新间隔", value: 30, default: 30,
  value_type: "integer", unit: "分钟", min: 5, max: 720, advanced: true,
};

beforeEach(async () => {
  i18n.addResourceBundle("zh", "ai_desktop", zh, true, true);
  await i18n.changeLanguage("zh");
});

it("distinguishes unknown quota from exhausted quota and preserves balances", () => {
  render(<MemoryRouter><SubscriptionDetailView mod={{ ...moduleInfo, detail: { providers: [{
    key: "bailian", name: "百炼", state: "ok", auth_type: "console", windows: [
      { label: "未知窗口", remaining_percent: null },
      { label: "每月", remaining_percent: 0 },
      { label: "账户余额", balance: 68.49, currency: "CNY" },
    ],
  }] } }} /></MemoryRouter>);
  expect(screen.queryByRole("progressbar", { name: "未知窗口" })).toBeNull();
  expect(screen.getByText("剩余 未知")).toBeTruthy();
  expect(screen.getByRole("progressbar", { name: "每月" }).getAttribute("aria-valuenow")).toBe("0");
  expect(screen.getByText("¥68.49")).toBeTruthy();
  expect(screen.getByText(zh.subscription.lowQuota)).toBeTruthy();
});

it("shows installation or console login instructions without asking for a model key", () => {
  const provider = { key: "bailian", name: "百炼", state: "no_credential", auth_type: "console" } as const;
  const { rerender } = render(<MemoryRouter><SubscriptionDetailView mod={{ ...moduleInfo, detail: { providers: [provider] } }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: zh.subscription.installGuide }).getAttribute("href")).toBe("https://bailian.aliyun.com/cli/install.md");
  rerender(<MemoryRouter><SubscriptionDetailView mod={{ ...moduleInfo, detail: { providers: [{ ...provider, source: "Bailian CLI" }] } }} /></MemoryRouter>);
  expect(screen.getByRole("link", { name: zh.subscription.manageAuthorization }).getAttribute("href")).toContain("providerKeys#credential-bailian_console");
});

it.each(["", "1.5", "4", "721"])("does not save invalid integer input %s", async (value) => {
  const onSave = vi.fn();
  render(<ConfigField item={config} disabled={false} onSave={onSave} />);
  const input = screen.getByRole("spinbutton");
  fireEvent.change(input, { target: { value } });
  fireEvent.blur(input);
  expect(onSave).not.toHaveBeenCalled();
});

it("follows refreshed config values while preserving unsaved edits", async () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<ConfigField item={config} disabled={false} onSave={onSave} />);
  const input = screen.getByRole<HTMLInputElement>("spinbutton");
  rerender(<ConfigField item={{ ...config, value: 60 }} disabled={false} onSave={onSave} />);
  expect(input.value).toBe("60");
  fireEvent.change(input, { target: { value: "90" } });
  rerender(<ConfigField item={{ ...config, value: 120 }} disabled={false} onSave={onSave} />);
  expect(input.value).toBe("90");
  await act(async () => fireEvent.blur(input));
  expect(onSave).toHaveBeenCalledWith("refresh", 90);
});

it("keeps concurrent module refresh states independent", async () => {
  let finishFirst: () => void = () => {};
  let finishSecond: () => void = () => {};
  const first = new Promise<void>((resolve) => { finishFirst = resolve; });
  const second = new Promise<void>((resolve) => { finishSecond = resolve; });
  const onRefresh = vi.fn((mod: DesktopModuleInfo) => mod.key === "subscription" ? first : second);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <section aria-label="first"><ModuleCard mod={moduleInfo} onRefresh={onRefresh} onSaveConfig={vi.fn()} onToggle={vi.fn()} /></section>
    <section aria-label="second"><ModuleCard mod={{ ...moduleInfo, key: "weather" }} onRefresh={onRefresh} onSaveConfig={vi.fn()} onToggle={vi.fn()} /></section>
  </QueryClientProvider>);
  const firstButton = within(screen.getByRole("region", { name: "first" })).getByRole<HTMLButtonElement>("button", { name: zh.module.refresh });
  const secondButton = within(screen.getByRole("region", { name: "second" })).getByRole<HTMLButtonElement>("button", { name: zh.module.refresh });
  await act(async () => { fireEvent.click(firstButton); fireEvent.click(secondButton); });
  await vi.waitFor(() => { expect(firstButton.disabled).toBe(true); expect(secondButton.disabled).toBe(true); });
  await act(async () => finishFirst());
  await vi.waitFor(() => expect(firstButton.disabled).toBe(false));
  expect(secondButton.disabled).toBe(true);
  await act(async () => finishSecond());
  await vi.waitFor(() => expect(secondButton.disabled).toBe(false));
  client.clear();
});
