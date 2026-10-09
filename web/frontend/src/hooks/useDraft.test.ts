import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useDraft } from "./useDraft";

describe("configuration drafts", () => {
  it("preserves edited fields while refreshing untouched fields", () => {
    const { result, rerender } = renderHook(({ source }) => useDraft(source), { initialProps: { source: { host: "old", port: 80 } } });
    act(() => result.current.update("host", "draft"));
    rerender({ source: { host: "remote", port: 443 } });
    expect(result.current.values).toEqual({ host: "draft", port: 443 });
    expect(result.current.patch).toEqual({ host: "draft" });
  });
  it("keeps edits made while a save is in progress", () => {
    const { result } = renderHook(() => useDraft({ name: "base" }));
    act(() => result.current.update("name", "submitted"));
    const submitted = result.current.patch;
    act(() => result.current.update("name", "newer edit"));
    act(() => result.current.acknowledge(submitted));
    expect(result.current.values.name).toBe("newer edit");
    expect(result.current.dirty).toBe(true);
  });
  it("acknowledges only successful fields in a partial save", () => {
    const { result } = renderHook(() => useDraft({ a: 0, b: 0 }));
    act(() => { result.current.update("a", 1); result.current.update("b", 2); });
    act(() => result.current.acknowledge({ a: 1 }));
    expect(result.current.patch).toEqual({ b: 2 });
  });
  it("does not mark restored values as dirty", () => {
    const { result } = renderHook(() => useDraft({ value: [1, 2] }));
    act(() => result.current.update("value", [1, 3]));
    act(() => result.current.update("value", [1, 2]));
    expect(result.current.dirty).toBe(false);
  });
  it("does not restore an old draft after the server acknowledges it", () => {
    const { result, rerender } = renderHook(({ source }) => useDraft(source), { initialProps: { source: { name: "base" } } });
    act(() => result.current.update("name", "saved"));
    rerender({ source: { name: "saved" } });
    rerender({ source: { name: "remote update" } });
    expect(result.current.values.name).toBe("remote update");
    expect(result.current.dirty).toBe(false);
  });
});
