import { expect, it } from "vitest";
import { parseMessageTags } from "./message-tags";

it("decodes the core tag grammar including escaped brackets, slashes and controls", () => {
  expect(parseMessageTags(String.raw`prefix [name:one\[two\]\\three\u000aend][channel:custom]`).map(({ key, value }) => ({ key, value }))).toEqual([
    { key: "name", value: "one[two]\\three\nend" }, { key: "channel", value: "custom" },
  ]);
});
it("preserves ordinary brackets and multiline prose while recognizing complete tags", () => {
  expect(parseMessageTags("[text] [https://example.com]\n[a:broken\nother] [file:x]").map((tag) => tag.key)).toEqual(["https", "file"]);
  expect(parseMessageTags("[a:broken\nother]")).toEqual([]);
});
