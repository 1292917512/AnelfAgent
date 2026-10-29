import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { CogneeStatus } from "@/lib/types";
import { ProjectionStatusCard } from "./ProjectionStatusCard";

it("offers retry for a missing consumer and startup errors while preserving stale and paused controls", async () => {
  await i18n.changeLanguage("en");
  const client = new QueryClient();
  const retry = vi.fn();
  const sync: CogneeStatus["sync"] = {
    enabled: true, running: false, pending: 2, failed: 0, synced: 3,
    last_error: "moved index validation failed",
  };
  const view = (state: CogneeStatus["sync"], stale = false) => <QueryClientProvider client={client}>
    <ProjectionStatusCard sync={state} updatedAt={0} stale={stale} enabled onRetry={retry} retrying={false} />
  </QueryClientProvider>;
  const { rerender } = render(view(sync));
  const label = i18n.t("memory:retryFailedSync");
  fireEvent.click(screen.getByRole("button", { name: label }));
  expect(retry).toHaveBeenCalledOnce();
  expect(screen.getByText(/moved index validation failed/)).toBeTruthy();
  rerender(view(sync, true));
  expect(screen.getByRole("button", { name: label }).hasAttribute("disabled")).toBe(true);
  rerender(view({ ...sync, running: true }));
  expect(screen.getByRole("button", { name: label })).toBeTruthy();
  rerender(view({ ...sync, enabled: false, last_error: "" }));
  expect(screen.queryByRole("button", { name: label })).toBeNull();
  expect(screen.queryByText(/The sync task is not running/)).toBeNull();
  client.clear();
});
