import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { approvalsApi } from "@/lib/api";
import i18n from "@/i18n";
import { useApprovalPopupStore, type ApprovalRequestPayload } from "@/stores/approval-popup-store";
import { ApprovalDialog } from "./ApprovalDialog";

const request: ApprovalRequestPayload = {
  request_id: "one", tool_name: "custom_tool", tool_args: '{"resource":"important.txt"}',
  risk_level: "medium", reason: "", received_at: Date.now(), timeout_seconds: 300,
};

afterEach(() => {
  cleanup();
  useApprovalPopupStore.setState({ queue: [] });
  vi.restoreAllMocks();
});

describe("approval decisions", () => {
  it("shows unknown tool arguments and retains a failed request for retry", async () => {
    await i18n.changeLanguage("en");
    const approve = vi.spyOn(approvalsApi, "approve").mockRejectedValueOnce(new Error("offline"));
    useApprovalPopupStore.setState({ queue: [request] });
    render(<ApprovalDialog />);
    expect(screen.getByText(request.tool_args)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: i18n.t("popup.allowOnce", { ns: "approvals" }) }));
    await waitFor(() => expect(approve).toHaveBeenCalledOnce());
    expect(useApprovalPopupStore.getState().queue).toHaveLength(1);
    expect(screen.getByRole("button", { name: i18n.t("popup.allowOnce", { ns: "approvals" }) }).hasAttribute("disabled")).toBe(false);
  });

  it("keeps the next approval busy when an earlier request finishes late", async () => {
    await i18n.changeLanguage("en");
    let rejectFirst: (reason: Error) => void = () => {};
    vi.spyOn(approvalsApi, "approve")
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectFirst = reject; }))
      .mockImplementationOnce(() => new Promise(() => {}));
    useApprovalPopupStore.setState({ queue: [request, { ...request, request_id: "two" }] });
    render(<ApprovalDialog />);
    const name = i18n.t("popup.allowOnce", { ns: "approvals" });
    await userEvent.click(screen.getByRole("button", { name }));
    act(() => useApprovalPopupStore.getState().dismiss("one"));
    await userEvent.click(screen.getByRole("button", { name }));
    await act(async () => rejectFirst(new Error("late failure")));
    expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true);
  });
});
