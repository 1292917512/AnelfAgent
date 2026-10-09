export const MAX_PREVIEW_ROWS = 1000;
export const MAX_PREVIEW_COLUMNS = 100;

export interface SheetPreview {
  name: string;
  html: string;
  truncated: boolean;
}
