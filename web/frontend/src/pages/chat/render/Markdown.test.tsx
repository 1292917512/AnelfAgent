import { render, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";
import { highlightCode } from "@/lib/shiki";

vi.mock("@/lib/shiki", () => ({ highlightCode: vi.fn(async () => "<pre><code>Highlighted</code></pre>") }));

it("preserves live code nodes across chunks and highlights only the settled result", async () => {
  const { container, rerender } = render(<Markdown live content={'Paragraph\n\n```python\nprint(1)\n```'} />);
  const paragraph = container.querySelector("p");
  const code = container.querySelector("pre");
  for (let i = 2; i <= 20; i++) rerender(<Markdown live content={`Paragraph\n\n\`\`\`python\nprint(${i})\n\`\`\``} />);
  expect(container.querySelector("p")).toBe(paragraph);
  expect(container.querySelector("pre")).toBe(code);
  expect(highlightCode).not.toHaveBeenCalled();
  rerender(<Markdown content={'Paragraph\n\n```python\nprint(20)\n```'} />);
  await waitFor(() => expect(highlightCode).toHaveBeenCalledOnce());
  expect(highlightCode).toHaveBeenCalledWith("print(20)", "python");
});
