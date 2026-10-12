import { describe, expect, it } from "vitest";
import { fileReferenceMarkdown, parseFileReference, resolveDocumentReference } from "./file-reference";

describe("file references", () => {
  it("resolves skill documentation links within the skill directory without changing explicit roots", () => {
    const base = { root: "workspace", path: "skills/deploy-check" } as const;
    expect(resolveDocumentReference("./scripts/check.py", base)).toEqual({ root: "workspace", path: "skills/deploy-check/scripts/check.py", isDir: false });
    expect(resolveDocumentReference("references/guide.md#steps", base)?.path).toBe("skills/deploy-check/references/guide.md");
    expect(resolveDocumentReference("../shared.md", base)?.path).toBe("skills/shared.md");
    expect(resolveDocumentReference("./project:config/settings.json", base)?.root).toBe("project");
    for (const href of ["../../../private", "https://example.com", "#steps", "%2Fetc/passwd", "javascript%3Aalert(1)"])
      expect(resolveDocumentReference(href, base)).toBeNull();
    expect(resolveDocumentReference("./note.md")?.path).toBe("note.md");
  });
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
