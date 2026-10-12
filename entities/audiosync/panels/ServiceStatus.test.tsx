import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosHeaders } from "axios";
import { beforeEach, expect, it, vi } from "vitest";
import i18n from "@/i18n";
import ServiceStatus from "./ServiceStatus";
import { audiosyncApi } from "./api";
import type { GpuStatus } from "./service-types";

const response = <T,>(data: T) => ({ data, status: 200, statusText: "OK", headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() } });

beforeEach(async () => { await i18n.changeLanguage("en"); });

function mount(reachable: boolean, status: GpuStatus = {}) {
  vi.spyOn(audiosyncApi, "funasrStatus").mockResolvedValue(response({ configured: reachable, reachable, endpoint: "" }));
  const gpu = vi.spyOn(audiosyncApi, "gpuStatus").mockResolvedValue(response(status));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ServiceStatus /></QueryClientProvider>);
  return gpu;
}

it("does not interpret an unconfigured service as released GPU models", async () => {
  const gpu = mount(false);
  await screen.findByText("Service unavailable. GPU model status cannot be determined.");
  expect(screen.queryByText("Released")).toBeNull();
  expect(screen.getByRole("button", { name: "Release all" }).hasAttribute("disabled")).toBe(true);
  expect(gpu).not.toHaveBeenCalled();
});

it("distinguishes missing workers from explicitly released workers", async () => {
  mount(true, { moss: { loaded: false }, asr: { loaded: true } });
  await screen.findByText("Loaded");
  expect(screen.getAllByText("Unknown")).toHaveLength(2);
  expect(screen.getAllByText("Released")).toHaveLength(1);
  expect(screen.getByRole("button", { name: "Release all" }).hasAttribute("disabled")).toBe(false);
});
