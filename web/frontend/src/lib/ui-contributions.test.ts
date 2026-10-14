import { describe, expect, it, vi } from "vitest";
import { defineUiContributions, loadUiContributions, type UiContribution } from "./ui-contributions";

const load = vi.fn(async () => ({ default: () => null }));
const entry: UiContribution = { id: "controls", slot: "workspace.tools", title: { ns: "module", key: "title" }, load };

describe("module UI contributions", () => {
  it("isolates broken modules and scopes identical IDs to their owners without loading views", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const result = await loadUiContributions({
      "entity:test": async () => ({ default: [entry] }),
      "channel:test": async () => ({ default: [{ ...entry, order: 10 }] }),
      "entity:broken": async () => { throw new Error("unavailable"); },
      "entity:duplicate": async () => ({ default: [entry, entry] }),
    });
    expect(result.map((item) => item.key)).toEqual(["channel:test/controls", "entity:test/controls"]);
    expect(load).not.toHaveBeenCalled();
  });
  it("rejects duplicate IDs and non-finite sorting values", () => {
    expect(() => defineUiContributions([entry, entry])).toThrow("duplicate");
    expect(() => defineUiContributions([{ ...entry, order: NaN }])).toThrow("order");
  });
  it("reserves core routes and tabs, and resolves module collisions deterministically", async () => {
    const route: UiContribution = { ...entry, id: "page", slot: "app.routes", path: "/example", group: "capabilities" };
    const result = await loadUiContributions({
      "entity:z": async () => ({ default: [route] }),
      "entity:a": async () => ({ default: [route, { ...route, id: "core", path: "/data" },
        { ...entry, id: "tab", slot: "data.tabs", tab: "database" }] }),
    }, ["app.routes:/data", "data.tabs:database"]);
    expect(result.map((item) => item.key)).toEqual(["entity:a/page"]);
  });
  it("loads the host with no modules", async () => {
    expect(await loadUiContributions({})).toEqual([]);
    expect(() => defineUiContributions([{ ...entry, slot: "app.routes", path: "/../data", group: "system" }])).toThrow("route");
  });
  it("registers typed activity summary actions without eagerly loading their views", async () => {
    const result = await loadUiContributions({
      "entity:sample": async () => ({ default: defineUiContributions([{ ...entry, slot: "activity.summary.actions", load }]) }),
    });
    expect(result.map((item) => item.slot)).toEqual(["activity.summary.actions"]);
    expect(load).not.toHaveBeenCalled();
  });
  it("allows independent tool renderers per owner and rejects duplicates within a module", async () => {
    const renderer: UiContribution = { ...entry, slot: "activity.tool", load };
    const result = await loadUiContributions({
      "entity:first": async () => ({ default: [renderer, { ...renderer, id: "duplicate" }] }),
      "channel:first": async () => ({ default: [renderer] }),
    });
    expect(result.map((item) => item.key)).toEqual(["channel:first/controls", "entity:first/controls"]);
    expect(load).not.toHaveBeenCalled();
  });
});
