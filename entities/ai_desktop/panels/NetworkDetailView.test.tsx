import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import i18n from "@/i18n";
import { NetworkDetailView } from "./NetworkDetailView";
import type { DesktopModuleInfo } from "./types";

it("distinguishes unknown, failed and reachable proxy observations without probing", async () => {
  await i18n.changeLanguage("en");
  const mod: DesktopModuleInfo = { key: "network", display_name: "Network", description: "", priority: 15,
    enabled: true, default_enabled: true, refresh_interval: 0, last_refresh: null, last_error: "",
    configs: [], current_content: null, detail: { network: { enabled: true, mode: "fallback", proxies: ["http://proxy.test:7890"], observations: [] } } };
  const { rerender } = render(<MemoryRouter><NetworkDetailView mod={mod} /></MemoryRouter>);
  expect(screen.getByRole("link").getAttribute("href")).toBe("/config?group=network%2Fproxy");
  expect(screen.queryByText(/ConnectTimeout/)).toBeNull();
  mod.detail.network!.observations = [
    { proxy: "http://proxy.test:7890", target: "https://api.test", state: "failed", error_code: "ConnectTimeout", status_code: null, observed_at: 1000, changed_at: 1000, failures: 1, recovered: false },
    { proxy: "http://proxy.test:7890", target: "https://site.test", state: "reachable", error_code: "", status_code: 503, observed_at: 1001, changed_at: 1001, failures: 0, recovered: false },
  ];
  rerender(<MemoryRouter><NetworkDetailView mod={mod} /></MemoryRouter>);
  expect(screen.getByText(/ConnectTimeout/)).toBeTruthy();
  expect(screen.getByText(/HTTP 503/).textContent).toContain(i18n.t("network.reachable", { ns: "ai_desktop" }));
});
