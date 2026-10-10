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
});
