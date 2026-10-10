import { useTranslation } from "react-i18next";
import { Paperclip, Send, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import type { WorkspaceSearchHit } from "@/lib/types";
import { RealtimeCallPanel, RealtimeCallToggle } from "./RealtimeCallBar";
import { detectMention, useMentionSearch } from "./mention/useMention";
import { MentionPanel } from "./mention/MentionPanel";
import { WorkspaceContextPreview } from "./WorkspaceContextPreview";
import { fileReferenceMarkdown } from "@/lib/file-reference";
import { useMediaQuery } from "@/lib/use-media-query";
import { ModelSelect } from "@/components/models/ModelSelect";
import { ContextChip } from "./ContextChip";
import { PendingAttachments } from "./PendingAttachments";

const PASTE_PLACEHOLDER_MIN_CHARS = 400;

/** 对话输入区：文本 + 附件 + 草稿注入 + 工作区文件拖入 + @提及 */
export function ChatInput() {
  const { t } = useTranslation("chat");
  const touchInput = useMediaQuery("(pointer: coarse)");
  const chatId = useChatStore((state) => state.activeChatId);
  const input = useChatStore((state) => state.buckets[chatId]?.inputDraft ?? "");
  const submitting = useChatStore((state) => state.buckets[chatId]?.submitting ?? false);
  const setDraft = useChatStore((state) => state.setInputDraft);
  const setInput = useCallback((text: string) => setDraft(chatId, text), [chatId, setDraft]);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const pendingFiles = useChatStore((s) => s.buckets[s.activeChatId]?.pendingFiles ?? []);
  const sending = useChatStore((s) => s.buckets[s.activeChatId]?.sending ?? false);
  const addFiles = useChatStore((s) => s.addFiles);
  const removeFile = useChatStore((s) => s.removeFile);
  const send = useChatStore((s) => s.send);
  const interrupt = useChatStore((s) => s.interrupt);

  const draftSeq = useWorkbenchStore((s) => s.draftSeq);
  const consumeDraft = useWorkbenchStore((s) => s.consumeDraft);

  // ── @提及 ──────────────────────────────────────────────────
  const [cursor, setCursor] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const [dismissedMention, setDismissedMention] = useState<string | null>(null);
  const mentionKey = JSON.stringify([chatId, input, cursor]);
  const mention = useMemo(() => dismissedMention === mentionKey ? null : detectMention(input, cursor), [input, cursor, dismissedMention, mentionKey]);
  const { items: mentionItems, loading: mentionLoading } = useMentionSearch(
    mention?.query ?? "", mention !== null,
  );
  useEffect(() => { setActiveIndex(0); }, [mention?.query]);

  const pickMention = useCallback((hit: WorkspaceSearchHit) => {
    if (!mention) return;
    const link = fileReferenceMarkdown({ path: hit.path, root: "workspace", isDir: false }, hit.name);
    const next = input.slice(0, mention.start) + link + input.slice(cursor);
    setInput(next);
    const pos = mention.start + link.length;
    setCursor(pos);
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(pos, pos);
    });
  }, [input, mention, cursor, setInput]);

  // AI ui_compose 草稿注入
  useEffect(() => {
    if (draftSeq === 0) return;
    const draft = consumeDraft();
    if (draft) {
      setInput(draft);
      inputRef.current?.focus();
    }
  }, [draftSeq, consumeDraft, setInput]);

  /** 输入框自动增高（上限约 8 行） */
  const autoGrow = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, []);

  useEffect(() => { autoGrow(); }, [input, autoGrow]);

  const handleSend = useCallback(async () => {
    await send(input, t("user"));
  }, [input, send, t]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.nativeEvent.isComposing) return;
    // 提及面板打开时优先消化导航键
    if (mention && mentionItems.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % mentionItems.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + mentionItems.length) % mentionItems.length);
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        pickMention(mentionItems[activeIndex]!);
        return;
      }
      if (e.key === "Escape") {
        setDismissedMention(mentionKey);
        e.preventDefault();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && (!touchInput || e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    const files: File[] = [];
    for (const item of Array.from(items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length) {
      e.preventDefault();
      const dt = new DataTransfer();
      files.forEach((f) => dt.items.add(f));
      addFiles(dt.files);
      return;
    }
    // 大段纯文本：插入占位符，全文作为附件（Codex 式 [Pasted Content N chars]）
    const text = e.clipboardData.getData("text/plain");
    if (text && text.length >= PASTE_PLACEHOLDER_MIN_CHARS) {
      e.preventDefault();
      const blob = new Blob([text], { type: "text/plain" });
      const file = new File([blob], "pasted.txt", { type: "text/plain" });
      const dt = new DataTransfer();
      dt.items.add(file);
      addFiles(dt.files);
      const el = inputRef.current;
      const at = el?.selectionStart ?? input.length;
      const placeholder = `[Pasted Content ${text.length} chars]`;
      setInput(input.slice(0, at) + placeholder + input.slice(el?.selectionEnd ?? at));
    }
  }, [addFiles, input, setInput]);

  return (
    <div className="chat-composer shrink-0">
      {/* 通话状态条（仅通话中显示：状态/转写/电平/音量） */}
      <RealtimeCallPanel />

      <WorkspaceContextPreview />
      <div className="mb-1 flex justify-end empty:hidden"><ContextChip /></div>

      {/* 输入卡片 */}
      <div className="composer-card relative border border-input rounded-2xl bg-card focus-within:border-ring transition-colors">
        {mention && (
          <MentionPanel
            items={mentionItems}
            loading={mentionLoading}
            activeIndex={activeIndex}
            onPick={pickMention}
            onHover={setActiveIndex}
          />
        )}
        <PendingAttachments files={pendingFiles} onRemove={removeFile} />
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setCursor(e.target.selectionStart ?? e.target.value.length);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onSelect={(e) => setCursor(e.currentTarget.selectionStart)}
          aria-label={t("messageInput")}
          placeholder={t(touchInput ? "placeholderTouch" : "placeholder")}
          rows={1}
          enterKeyHint={touchInput ? "enter" : "send"}
          className="w-full resize-none bg-transparent p-3 text-sm text-foreground placeholder:text-muted outline-none max-h-[180px]"
        />
        <div className="composer-actions flex flex-wrap items-center justify-between gap-2 px-3 pb-2">
          <div className="flex min-w-0 items-center gap-1">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
            />
            <Button variant="ghost" size="icon" onClick={() => fileInputRef.current?.click()} title={t("attachFiles")}>
              <Paperclip size={18} />
            </Button>
            <RealtimeCallToggle />
            <ModelSelect modelType="chat" compact className="composer-model" />
          </div>
          <div className="flex items-center gap-2">
            {sending && <Button variant="danger" size="sm" onClick={() => void interrupt()} title={t("stopTitle")}>
              <Square size={13} />{t("stop")}
            </Button>}
            <Button variant="primary" size="sm" onClick={() => void handleSend()} loading={submitting}
              disabled={(!input.trim() && !pendingFiles.some((file) => file.path)) || pendingFiles.some((file) => file.uploading)}>
              <Send size={15} />{t(sending ? "addMessage" : "send")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
