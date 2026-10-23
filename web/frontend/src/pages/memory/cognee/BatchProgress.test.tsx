import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";
import i18n from "@/i18n";
import type { CogneeBatchProgress } from "@/lib/types";
import { BatchProgress } from "./BatchProgress";

const documents = { total: 206, completed: 26, fragments: 202, fragments_completed: 22, last_completed_at: 1398 };
const active: CogneeBatchProgress = {
  stage: "cognify", items: 5, attempt: 1, dataset_name: "relations", started_at: 1000,
  elapsed_seconds: 400, stage_elapsed_seconds: 390, quiet_seconds: 2, last_change_at: 1398,
  timeout_seconds: 3600, progress_checked_at: 1400, progress_error: "", documents_checked_at: 1400, documents,
  pipeline: { status: "DATASET_PROCESSING_STARTED", completed_items: 1, total_items: 2885, current_stage: "" },
};

it("shows actual batch completions even while SDK traversal is stuck at one", async () => {
  await i18n.changeLanguage("en");
  const { container, rerender } = render(<BatchProgress active={active} stale={false} />);
  expect(screen.getByText("26 / 206")).toBeTruthy();
  expect(screen.getByText("22 / 202")).toBeTruthy();
  expect(container.querySelector("details")?.open).toBe(false);
  expect(screen.queryByText(/No new batch completion marker/)).toBeNull();
  rerender(<BatchProgress active={{ ...active, documents: { ...documents, completed: 34, fragments_completed: 30 } }} stale={false} />);
  expect(screen.getByText("30 / 202")).toBeTruthy();
  expect(screen.getByRole("progressbar").getAttribute("value")).toBe("34");
  expect(screen.getByRole("progressbar").getAttribute("max")).toBe("206");
});

it("does not turn an unavailable observation into zero or a deadlock warning", async () => {
  await i18n.changeLanguage("en");
  const { rerender } = render(<BatchProgress active={{ ...active, documents: null, quiet_seconds: 600 }} stale={false} />);
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.queryByText(/No new batch completion marker/)).toBeNull();
  rerender(<BatchProgress active={{ ...active, quiet_seconds: 600, progress_error: "database busy" }} stale={false} />);
  expect(screen.getByText("22 / 202")).toBeTruthy();
  expect(screen.getByRole("alert").textContent).toContain("database busy");
  expect(screen.queryByText(/No new batch completion marker/)).toBeNull();
});

it("distinguishes extracted documents from safe commit and suppresses stale inactivity warnings", async () => {
  await i18n.changeLanguage("en");
  const { rerender } = render(<BatchProgress active={{ ...active, quiet_seconds: 600 }} stale />);
  expect(screen.queryByText(/No new batch completion marker/)).toBeNull();
  rerender(<BatchProgress active={{ ...active, documents: { ...documents, completed: 206,
    fragments_completed: 202 }, quiet_seconds: 600 }} stale={false} />);
  expect(screen.getByRole("status").textContent).toContain("awaiting pipeline wrap-up, validation and a safe commit");
  expect(screen.queryByText(/No new batch completion marker/)).toBeNull();
});
