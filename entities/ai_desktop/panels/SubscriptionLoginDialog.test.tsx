import { StrictMode } from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import { SubscriptionLoginDialog } from "./SubscriptionLoginDialog";
import zh from "./locales/zh.json";

const { aiDesktopApi } = vi.hoisted(() => ({ aiDesktopApi: {
  startLogin: vi.fn(), loginStatus: vi.fn(), loginImage: vi.fn(), cancelLogin: vi.fn(), confirmLogin: vi.fn(),
} }));
vi.mock("./api", () => ({ aiDesktopApi }));

const state = { id: "session", state: "waiting" as const, expires_at: 0, error: "" };

beforeEach(async () => {
  vi.useFakeTimers();
  i18n.addResourceBundle("zh", "ai_desktop", zh, true, true);
  await i18n.changeLanguage("zh");
  vi.mocked(aiDesktopApi.startLogin).mockResolvedValue({ data: state });
  vi.mocked(aiDesktopApi.loginStatus).mockResolvedValue({ data: state });
  vi.mocked(aiDesktopApi.loginImage).mockResolvedValue({ data: new Blob(["image"]) });
  vi.mocked(aiDesktopApi.cancelLogin).mockResolvedValue({ data: {} });
  vi.stubGlobal("URL", class extends URL {
    static createObjectURL = vi.fn(() => "blob:test");
    static revokeObjectURL = vi.fn();
  });
});

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<StrictMode><QueryClientProvider client={client}>
    <SubscriptionLoginDialog onClose={vi.fn()} />
  </QueryClientProvider></StrictMode>);
  return { ...view, client };
}

it("starts once in StrictMode, releases the image and stops polling on close", async () => {
  const view = mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(aiDesktopApi.startLogin).toHaveBeenCalledTimes(1);
  expect(aiDesktopApi.cancelLogin).not.toHaveBeenCalled();
  expect(screen.getByRole("img", { name: zh.subscription.loginQr })).toBeTruthy();
  view.unmount();
  expect(aiDesktopApi.cancelLogin).toHaveBeenCalledWith("session");
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test");
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(aiDesktopApi.loginStatus).toHaveBeenCalledTimes(1);
  view.client.clear();
});

it.each(["complete", "expired", "error"] as const)("stops polling in state %s", async (status) => {
  vi.mocked(aiDesktopApi.loginStatus).mockResolvedValue({ data: { ...state, state: status,
    error: "" } });
  const view = mount();
  const invalidate = vi.spyOn(view.client, "invalidateQueries");
  await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
  expect(screen.getByText(zh.subscription.loginStates[status])).toBeTruthy();
  expect(aiDesktopApi.loginStatus).toHaveBeenCalledTimes(1);
  expect(aiDesktopApi.loginImage).not.toHaveBeenCalled();
  expect(invalidate).toHaveBeenCalledTimes(status === "complete" ? 2 : 0);
  view.unmount();
  view.client.clear();
});

it("shows automatic preparation, QR and explicit authorization without a page click", async () => {
  aiDesktopApi.loginStatus.mockResolvedValue({ data: { ...state, state: "preparing" } });
  const view = mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(screen.getByRole("status").textContent).toBe(zh.subscription.loginStates.preparing);
  expect(screen.queryByText(/uv sync/)).toBeNull();
  expect(aiDesktopApi.loginImage).not.toHaveBeenCalled();
  aiDesktopApi.loginStatus.mockResolvedValue({ data: state });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.getByRole("img", { name: zh.subscription.loginQr })).toBeTruthy();
  aiDesktopApi.loginStatus.mockResolvedValue({ data: { ...state, state: "confirming" } });
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(screen.queryByRole("img")).toBeNull();
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: zh.subscription.confirmLogin })); });
  expect(aiDesktopApi.confirmLogin).toHaveBeenCalledWith("session");
  view.unmount();
  view.client.clear();
});
