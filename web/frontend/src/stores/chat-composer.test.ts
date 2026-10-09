import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { chatApi } from "@/lib/api";
import { useChatStore } from "./chat-store";
import { clearSendWatchdog, emptyBucket } from "./chat-shared";

const accepted: AxiosResponse = { data: {}, status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } };
beforeEach(() => {
  useChatStore.setState({ activeChatId: "A", buckets: { A: { ...emptyBucket(), inputDraft: "First" }, B: emptyBucket() } });
});
afterEach(() => { clearSendWatchdog(); vi.restoreAllMocks(); });

it("preserves newer text and another conversation while an earlier send completes", async () => {
  let release!: (value: AxiosResponse) => void;
  vi.spyOn(chatApi, "send").mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  const request = useChatStore.getState().send("First", "User");
  useChatStore.getState().setInputDraft("A", "Next");
  useChatStore.setState({ activeChatId: "B" });
  useChatStore.getState().setInputDraft("B", "Other draft");
  release(accepted);
  expect(await request).toBe(true);
  expect(useChatStore.getState().buckets.A?.inputDraft).toBe("Next");
  expect(useChatStore.getState().buckets.B?.inputDraft).toBe("Other draft");
});

it("restores attachments on failure without discarding newly attached files or duplicating optimistic messages", async () => {
  let reject!: (error: Error) => void;
  vi.spyOn(chatApi, "send").mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
  useChatStore.getState().attachWorkspaceFile("first.txt", "first.txt");
  const request = useChatStore.getState().send("First", "User");
  useChatStore.getState().attachWorkspaceFile("next.txt", "next.txt");
  reject(new Error("offline"));
  expect(await request).toBe(false);
  const bucket = useChatStore.getState().buckets.A;
  expect(bucket?.pendingFiles.map((file) => file.path)).toEqual(["first.txt", "next.txt"]);
  expect(bucket?.inputDraft).toBe("First");
  expect(bucket?.messages.filter((message) => message.role === "user")).toEqual([]);
  expect(bucket?.submitting).toBe(false);
});

it("blocks duplicate submission until acknowledgement and clears only the acknowledged draft", async () => {
  let release!: (value: AxiosResponse) => void;
  const send = vi.spyOn(chatApi, "send").mockImplementation(() => new Promise((resolve) => { release = resolve; }));
  const request = useChatStore.getState().send("First", "User");
  expect(await useChatStore.getState().send("First", "User")).toBe(false);
  release(accepted);
  await request;
  expect(send).toHaveBeenCalledOnce();
  expect(useChatStore.getState().buckets.A?.inputDraft).toBe("");
});

it("does not resurrect a removed chat when its send request fails", async () => {
  let reject!: (error: Error) => void;
  vi.spyOn(chatApi, "send").mockImplementation(() => new Promise((_resolve, fail) => { reject = fail; }));
  const request = useChatStore.getState().send("First", "User");
  useChatStore.setState({ buckets: {}, activeChatId: "B" });
  reject(new Error("offline"));
  await request;
  expect(useChatStore.getState().buckets.A).toBeUndefined();
});

it("copies submitted content into the owning draft without claiming to withdraw it", () => {
  useChatStore.setState({ buckets: { A: { ...emptyBucket(), inputDraft: "Next", messages: [
    { role: "user", content: "Already submitted", cid: "message", delivery: "submitted" },
  ] } } });
  useChatStore.getState().copyMessageToDraft("message");
  const bucket = useChatStore.getState().buckets.A;
  expect(bucket?.messages).toHaveLength(1);
  expect(bucket?.messages[0]?.delivery).toBe("submitted");
  expect(bucket?.inputDraft).toBe("Next\n\nAlready submitted");
});

it("does not attach workspace context when it is disabled for the conversation", async () => {
  const send = vi.spyOn(chatApi, "send").mockResolvedValue(accepted);
  useChatStore.getState().setWorkspaceContextEnabled("A", false);
  await useChatStore.getState().send("First", "User");
  expect(send.mock.calls[0]?.[5]).toBeUndefined();
  expect(useChatStore.getState().buckets.B?.workspaceContextEnabled).toBe(true);
});
