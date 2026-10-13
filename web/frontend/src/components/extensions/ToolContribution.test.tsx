import { render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { loadUiContributions, type ToolRendererProps } from "@/lib/ui-contributions";
import { getUiContributions } from "@/lib/ui-contribution-registry";
import { ToolContribution } from "./ToolContribution";

vi.mock("@/lib/ui-contribution-registry", () => ({ getUiContributions: vi.fn() }));
const props: ToolRendererProps = {
  entry: { id: "call", kind: "tool", owner: "entity:demo", name: "run", arguments: "{}", targets: [], request_id: "request", ts: 1, status: "done", result: "Original receipt" },
  view: "detail", fallback: <p>Original receipt</p>,
};

it("does not select another module's renderer when the owner is missing", () => {
  vi.mocked(getUiContributions).mockReturnValue([]);
  render(<ToolContribution {...props} />);
  expect(screen.getByText("Original receipt")).toBeTruthy();
});

it.each(["load", "render"])("keeps the receipt available when module %s fails", async (failure) => {
  vi.spyOn(console, "error").mockImplementation(() => {});
  const ignoreExpectedError = (event: ErrorEvent) => {
    if (["Chunk unavailable", "Invalid module data"].includes(event.message)) event.preventDefault();
  };
  window.addEventListener("error", ignoreExpectedError);
  try {
    const entries = await loadUiContributions({ "entity:demo": async () => ({ default: [
      { id: "view", slot: "activity.tool", title: { ns: "workbench", key: "activity.result" }, load: async () => {
        if (failure === "load") throw new Error("Chunk unavailable");
        return { default: function Broken() { throw new Error("Invalid module data"); } };
      } },
    ] }) });
    vi.mocked(getUiContributions).mockReturnValue(entries);
    render(<ToolContribution {...props} />);
    await waitFor(() => expect(console.error).toHaveBeenCalled());
    expect(screen.getByText("Original receipt")).toBeTruthy();
  } finally {
    window.removeEventListener("error", ignoreExpectedError);
  }
});
