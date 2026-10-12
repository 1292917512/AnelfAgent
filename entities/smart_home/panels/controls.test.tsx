import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ValueSlider } from "./DeviceControls";

it("sends a keyboard slider commit once and restores observed state on failure", async () => {
  const commit = vi.fn().mockResolvedValue(false);
  render(<ValueSlider label="Volume" value={25} disabled={false} onCommit={commit} />);
  const slider = screen.getByRole("slider") as HTMLInputElement;
  fireEvent.change(slider, { target: { value: "40" } });
  fireEvent.keyUp(slider, { key: "ArrowRight" });
  fireEvent.pointerUp(slider);
  await waitFor(() => expect(slider.value).toBe("25"));
  expect(commit).toHaveBeenCalledExactlyOnceWith(40);
});

it("a later snapshot replaces a pending slider draft", async () => {
  const commit = vi.fn().mockResolvedValue(true);
  const { rerender } = render(<ValueSlider label="Brightness" value={30} disabled={false} onCommit={commit} />);
  const slider = screen.getByRole("slider") as HTMLInputElement;
  fireEvent.change(slider, { target: { value: "60" } });
  fireEvent.pointerUp(slider);
  await waitFor(() => expect(commit).toHaveBeenCalledExactlyOnceWith(60));
  rerender(<ValueSlider label="Brightness" value={55} disabled={false} onCommit={commit} />);
  expect(slider.value).toBe("55");
});
