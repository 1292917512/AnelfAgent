import { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChoiceField, SchemaField } from "./SchemaField";

describe("structured configuration fields", () => {
  it("edits checkbox arrays and preserves unknown selections", () => {
    const changed = vi.fn();
    render(<ChoiceField label="Events" options={["push", "issues"]} value={["push", "future_event"]} onChange={changed} />);
    fireEvent.click(screen.getByLabelText("issues"));
    expect(changed).toHaveBeenLastCalledWith(["push", "future_event", "issues"]);
    fireEvent.click(screen.getByLabelText("push"));
    expect(changed).toHaveBeenLastCalledWith(["future_event"]);
  });

  it("distinguishes inherited content from an explicitly empty selection", () => {
    const changed = vi.fn();
    render(<SchemaField schema={{ anyOf: [{ type: "array", items: { type: "string" } }, { type: "null" }], options: ["body", "url"], default: null }}
      value={null} label="Content" onChange={changed} />);
    fireEvent.click(screen.getByRole("switch", { name: "Use inherited settings" }));
    expect(changed).toHaveBeenLastCalledWith([]);
  });

  it("adds referenced objects and retains typed false and zero values", () => {
    function Form() {
      const [value, setValue] = useState<unknown>([]);
      return <><SchemaField label="Repositories" value={value} onChange={setValue} schema={{ type: "array", items: { $ref: "#/$defs/Repo" }, $defs: {
        Repo: { type: "object", properties: { owner: { type: "string" }, priority: { type: "boolean", default: true }, interval: { type: "integer", default: 60 }, events: { type: "array", options: ["push", "issues"] } } },
      } }} /><output data-testid="value">{JSON.stringify(value)}</output></>;
    }
    render(<Form />);
    fireEvent.click(screen.getByText("Add Repositories"));
    fireEvent.change(screen.getByLabelText("owner"), { target: { value: "nonebot" } });
    fireEvent.click(screen.getByRole("switch", { name: "priority" }));
    fireEvent.change(screen.getByLabelText("interval"), { target: { value: "0" } });
    fireEvent.click(screen.getByLabelText("push"));
    expect(JSON.parse(screen.getByTestId("value").textContent ?? "[]")).toEqual([{ owner: "nonebot", priority: false, interval: 0, events: ["push"] }]);
    fireEvent.click(screen.getByRole("button", { name: "Remove Repositories entry 1" }));
    expect(screen.getByTestId("value").textContent).toBe("[]");
  });
});
