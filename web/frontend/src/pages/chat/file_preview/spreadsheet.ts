import { read, utils } from "xlsx";

import { MAX_PREVIEW_ROWS, MAX_PREVIEW_COLUMNS, type SheetPreview } from "./spreadsheet-types";

export function parseSpreadsheet(buffer: ArrayBuffer): SheetPreview[] {
  const workbook = read(buffer, { type: "array", sheetRows: MAX_PREVIEW_ROWS, cellHTML: false });
  return workbook.SheetNames.flatMap((name) => {
    const sheet = workbook.Sheets[name];
    if (!sheet) return [];
    const ref = sheet["!ref"];
    let truncated = false;
    if (ref) {
      const range = utils.decode_range(ref);
      const fullRange = utils.decode_range(sheet["!fullref"] ?? ref);
      truncated = fullRange.e.r >= MAX_PREVIEW_ROWS || range.e.c - range.s.c + 1 > MAX_PREVIEW_COLUMNS;
      sheet["!ref"] = utils.encode_range({
        s: range.s, e: { r: Math.min(range.e.r, MAX_PREVIEW_ROWS - 1), c: Math.min(range.e.c, range.s.c + MAX_PREVIEW_COLUMNS - 1) },
      });
    }
    return [{ name, html: utils.sheet_to_html(sheet), truncated }];
  });
}
