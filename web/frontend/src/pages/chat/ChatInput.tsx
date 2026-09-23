import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { FileText, Image as ImageIcon, Loader2, Music, Paperclip, Send, Square, Video, X } from "lucide-react";
import { Button } from "@/components/ui";
import { useChatStore } from "@/stores/chat-store";
import { useWorkbenchStore } from "@/stores/workbench-store";
import type { WorkspaceSearchHit } from "@/lib/types";
import { RealtimeCallPanel, RealtimeCallProvider, RealtimeCallToggle } from "./RealtimeCallBar";
import { detectMention, useMentionSearch } from "./mention/useMention";
import { MentionPanel } from "./mention/MentionPanel";
import { mentionMarkdown } from "./mention/mentionMarkdown";

const FILE_TYPE_ICONS: Record<string, typeof FileText> = {
  image: ImageIcon,
  audio: Music,
  video: Video,
  file: FileText,
};

/** 工作区文件拖拽的自定义 MIME 类型 */
export const WORKSPACE_FILE_MIME = "application/x-workspace-file";

/** 大段粘贴转占位符的字符阈值（Codex 式：全文暂存，文本框只留占位符） */
const PASTE_PLACEHOLDER_MIN_CHARS = 400;

/** 对话输入区：文本 + 附件 + 草稿注入 + 工作区文件拖入 + @提及 */
export function ChatInput() {
  const { t } = useTranslation("chat");
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const pendingFiles = useChatStore((s) => s.buckets[s.activeChatId]?.pendingFiles ?? []);
  const sending = useChatStore((s) => s.buckets[s.activeChatId]?.sending ?? false);
  const addFiles = useChatStore((s) => s.addFiles);
  const removeFile = useChatStore((s) => s.removeFile);
  const attachWorkspaceFile = useChatStore((s) => s.attachWorkspaceFile);
  const send = useChatStore((s) => s.send);
  const interrupt = useChatStore((s) => s.interrupt);

  const draftSeq = useWorkbenchStore((s) => s.draftSeq);
  const consumeDraft = useWorkbenchStore((s) => s.consumeDraft);

  // ── @提及 ──────────────────────────────────────────────────
  const [cursor, setCursor] = useState(0);
  const [activeIndex, setActiveIndex] = useState(0);
  const mention = useMemo(() => detectMention(input, cursor), [input, cursor]);
  const { items: mentionItems, loading: mentionLoading } = useMentionSearch(
    mention?.query ?? "", mention !== null,
  );
  useEffect(() => { setActiveIndex(0); }, [mention?.query]);

  const pickMention = useCallback((hit: WorkspaceSearchHit) => {
    if (!mention) return;
    const link = mentionMarkdown(hit.name, hit.path);
    const next = input.slice(0, mention.start) + link + input.slice(cursor);
    setInput(next);
    const pos = mention.start + link.length;
    requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(pos, pos);
    });
  }, [input, mention, cursor]);

  // AI ui_compose 草稿注入
  useEffect(() => {
    if (draftSeq === 0) return;
    const draft = consumeDraft();
    if (draft) {
      setInput(draft);
      inputRef.current?.focus();
    }
  }, [draftSeq, consumeDraft]);

  /** 输入框自动增高（上限约 8 行） */
  const autoGrow = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, []);

  useEffect(() => { autoGrow(); }, [input, autoGrow]);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    const ok = await send(text, t("user"));
    if (ok) setInput("");
  }, [input, send, t]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
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
        e.preventDefault();
        setCursor(-1); // 退出提及态（detectMention 命中不到负光标）
        setInput((v) => v); // 触发重算
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    // 工作区文件树拖入
    const wsData = e.dataTransfer.getData(WORKSPACE_FILE_MIME);
    if (wsData) {
      try {
        const { path, name } = JSON.parse(wsData) as { path: string; name: string };
        attachWorkspaceFile(path, name);
        return;
      } catch { /* 数据异常时按普通文件处理 */ }
    }
    addFiles(e.dataTransfer.files);
  }, [addFiles, attachWorkspaceFile]);

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
  }, [addFiles, input]);

  return (
    <RealtimeCallProvider>
    <div className="shrink-0">
      {/* 待发送文件预览 */}
      {pendingFiles.length > 0 && (
        <div className="flex gap-2 py-2 overflow-x-auto">
          {pendingFiles.map((pf, idx) => {
            const Icon = FILE_TYPE_ICONS[pf.type] || FileText;
            return (
              <div key={`${pf.file.name}-${idx}`} className="relative flex-shrink-0 group">
                {pf.preview ? (
                  <img src={pf.preview} alt="" className="w-16 h-16 rounded-md object-cover border border-border" />
                ) : (
                  <div className="w-16 h-16 rounded-md border border-border bg-elevated flex flex-col items-center justify-center gap-1">
                    <Icon size={16} className="text-muted" />
                    <span className="text-[9px] text-muted truncate max-w-[56px]">{pf.file.name}</span>
                  </div>
                )}
                {pf.uploading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 rounded-md">
                    <Loader2 size={16} className="text-white animate-spin" />
                  </div>
                )}
                <button
                  onClick={() => removeFile(idx)}
                  className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-danger text-white flex items-center justify-center transition-opacity opacity-100 md:opacity-0 md:group-hover:opacity-100"
                >
                  <X size={10} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 通话状态条（仅通话中显示：状态/转写/电平/音量） */}
      <RealtimeCallPanel />

      {/* 输入卡片 */}
      <div
        className="relative border border-input rounded-lg bg-card focus-within:border-ring transition-colors"
        onDrop={handleDrop}
        onDragOver={(e) => e.preventDefault()}
      >
        {mention && (
          <MentionPanel
            items={mentionItems}
            loading={mentionLoading}
            activeIndex={activeIndex}
            onPick={pickMention}
            onHover={setActiveIndex}
          />
        )}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setCursor(e.target.selectionStart ?? e.target.value.length);
          }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          onSelect={(e) => setCursor((e.target as HTMLTextAreaElement).selectionStart)}
          placeholder={t("placeholder")}
          rows={1}
          className="w-full resize-none bg-transparent p-3 text-sm text-foreground placeholder:text-muted outline-none max-h-[180px]"
        />
        <div className="flex items-center justify-between px-3 pb-2">
          <div className="flex items-center gap-2">
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
          </div>
          {sending ? (
            <Button
              variant="danger"
              size="sm"
              onClick={() => void interrupt()}
              title={t("stopTitle")}
            >
              <Square size={13} />
              {t("stop")}
            </Button>
          ) : (
            <Button
              variant="primary"
              size="sm"
              onClick={handleSend}
              disabled={!input.trim() && !pendingFiles.some((f) => f.path)}
            >
              <Send size={15} />
              {t("send")}
            </Button>
          )}
        </div>
      </div>
    </div>
    </RealtimeCallProvider>
  );
}
