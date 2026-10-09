import { parseSpreadsheet } from "./spreadsheet";

self.onmessage = (event: MessageEvent<ArrayBuffer>) => {
  try { self.postMessage({ sheets: parseSpreadsheet(event.data) }); }
  catch { self.postMessage({ error: true }); }
};
