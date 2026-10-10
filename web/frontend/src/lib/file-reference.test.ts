import { describe, expect, it } from "vitest";
import { fileReferenceMarkdown, parseFileReference } from "./file-reference";

describe("file references", () => {
  it.each(["workspace", "project"] as const)("roundtrips special characters in the %s root", (root) => {
    const reference = { root, path: "资料 [草稿]/test (1)#20%.txt", isDir: false };
    const markdown = fileReferenceMarkdown(reference, "test [草稿] (1).txt");
    const target = markdown.slice(markdown.lastIndexOf("](") + 2, -1);
    expect(parseFileReference(target)).toEqual(reference);
    expect(markdown).toContain("\\[草稿\\]");
  });
  it("preserves directory identity", () => {
    expect(parseFileReference("./project%3Adir%3Adocs/new%20folder")).toEqual({ root: "project", path: "docs/new folder", isDir: true });
  });
  it.each(["./../secret", "./%2e%2e/secret", "./project%3AC%3A/secret", "./%2Fetc/passwd", "./a%00b", "./%FF", "https://example.com"])("rejects invalid reference %s", (value) => {
    expect(parseFileReference(value)).toBeNull();
  });
});
