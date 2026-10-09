import { describe, expect, it } from "vitest";
import { utils, write } from "xlsx";
import { parseSpreadsheet } from "./spreadsheet";

describe("spreadsheet preview", () => {
  it("limits wide and long sheets and preserves all sheet names", () => {
    const workbook = utils.book_new();
    const sheet = utils.aoa_to_sheet([["first", "second"]]);
    sheet["A1500"] = { t: "s", v: "outside row limit" };
    sheet["DW1"] = { t: "s", v: "outside column limit" };
    sheet["!ref"] = "A1:DW1500";
    utils.book_append_sheet(workbook, sheet, "Report");
    utils.book_append_sheet(workbook, utils.aoa_to_sheet([["Other sheet"]]), "Notes");
    const result = parseSpreadsheet(write(workbook, { type: "array", bookType: "xlsx" }));
    expect(result.map((item) => item.name)).toEqual(["Report", "Notes"]);
    expect(result[0]?.truncated).toBe(true);
    expect(result[0]?.html).toContain("first");
    expect(result[0]?.html).not.toContain("outside");
    expect(result[1]?.truncated).toBe(false);
  });
});
