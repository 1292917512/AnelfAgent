import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";
import i18n from "@/i18n";
import type { RecallTestItem } from "@/lib/types";
import { RecallAttribution } from "./RecallAttribution";

it("keeps provenance collapsed and distinguishes the subject from the material conversation", async () => {
  await i18n.changeLanguage("zh");
  const item: RecallTestItem = {
    id: "mem:7", source: "memory", score: .8, content: "乙喜欢安静", type: "semantic",
    tags: ["user:telegram:2"], dataset: "", path: "", sensitivity: "private",
    provenance: { origins: [{ kind: "conversation_summary", scope: "group_qq:3", message_ids: [41, 42] }] },
  };
  const { container } = render(<RecallAttribution item={item} />);
  expect(screen.queryByText("group_qq:3")).toBeNull();
  expect(screen.queryByText("user:telegram:2")).toBeNull();
  const summary = container.querySelector("summary");
  if (!summary) throw new Error("Missing attribution control");
  const user = userEvent.setup();
  await user.click(summary);
  await waitFor(() => expect(screen.getByText("group_qq:3")).toBeTruthy());
  expect(screen.getByText("user:telegram:2")).toBeTruthy();
  expect(screen.getByText(/41, 42/)).toBeTruthy();
  await user.click(summary);
  await waitFor(() => expect(screen.queryByText("group_qq:3")).toBeNull());
});
