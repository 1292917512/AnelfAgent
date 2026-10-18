import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useAppStore } from "@/stores/app-store";
import { CommandPaletteHost } from "./CommandPaletteHost";

vi.mock("./CommandPalette", () => ({
  CommandPalette: () => <div>Loaded command palette</div>,
}));

afterEach(() => {
  cleanup();
  useAppStore.setState({ paletteOpen: false });
});

it("loads the palette on demand and preserves the global shortcut", async () => {
  render(<CommandPaletteHost />);
  expect(screen.queryByText("Loaded command palette")).toBeNull();

  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  expect(await screen.findByText("Loaded command palette")).toBeTruthy();
  expect(useAppStore.getState().paletteOpen).toBe(true);

  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  await waitFor(() => expect(screen.queryByText("Loaded command palette")).toBeNull());
  expect(useAppStore.getState().paletteOpen).toBe(false);
});

it("loads the palette when opened through the shared store action", async () => {
  render(<CommandPaletteHost />);
  act(() => useAppStore.getState().setPaletteOpen(true));
  expect(await screen.findByText("Loaded command palette")).toBeTruthy();
});
