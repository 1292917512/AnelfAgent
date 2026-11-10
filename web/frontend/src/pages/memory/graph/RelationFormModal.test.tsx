import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import i18n from "@/i18n";
import type { GraphEdge } from "@/lib/types";
import { RelationFormModal } from "./RelationFormModal";

it("requires a review basis and submits the revision that was displayed", async () => {
  await i18n.changeLanguage("zh");
  const edge: GraphEdge = {
    id: 1, subject: { id: 1, node_key: "topic:a", node_type: "topic", label: "甲" },
    object: { id: 2, node_key: "topic:b", node_type: "topic", label: "乙" },
    predicate: "属于", evidence: "原始材料", strength: 0.99, symmetric: false,
    verification: "pending", revision: "read-revision", origin: "heartbeat_extract",
    source_memory_id: null, metadata: {}, created: 1, updated: 1, archived: false,
  };
  const onSubmit = vi.fn(async () => {});
  const onClose = vi.fn();
  const user = userEvent.setup();
  const view = render(<RelationFormModal open edge={edge} onSubmit={onSubmit} onClose={onClose} />);
  try {
    await user.selectOptions(screen.getByRole("combobox"), "verified");
    const submit = screen.getByRole("button", { name: "确定" });
    await user.click(submit);
    expect(onSubmit).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText(/核验依据/), "已与原始来源交叉核对");
    await user.click(submit);
    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
      verification: "verified", verification_reason: "已与原始来源交叉核对", expected_revision: "read-revision",
    })));
    expect(onClose).toHaveBeenCalledOnce();
  } finally {
    view.unmount();
  }
});
