import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { chatApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Send, Loader2, Trash2, Paperclip, X, FileText, Image as ImageIcon, Music, Video } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Button } from "@/components/ui";
import { ModelSelect } from "@/components/models/ModelSelect";

interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  id?: number;
  /** 客户端生成的稳定 key（SSE 消息无服务端 id 时使用） */
  cid?: string;
  media_type?: string;
  url?: string;
  caption?: string;
}

interface PendingFile {
  file: File;
  preview?: string;
  type: string;
  uploading: boolean;
  path?: string;
}

const FILE_TYPE_ICONS: Record<string, typeof FileText> = {
  image: ImageIcon,
  audio: Music,
  video: Video,
  file: FileText,
};

function classifyFile(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (["jpg", "jpeg", "png", "gif", "webp", "bmp", "svg"].includes(ext)) return "image";
  if (["mp3", "wav", "ogg", "flac", "m4a", "amr", "opus"].includes(ext)) return "audio";
  if (["mp4", "avi", "mkv", "mov", "webm", "flv"].includes(ext)) return "video";
  return "file";
}

function MediaBubble({ msg }: { msg: ChatMessage }) {
  const { t } = useTranslation("chat");
  const mt = msg.media_type;
  const url = msg.url || "";
  if (mt === "image" && url)
    return <img src={url} alt={msg.caption || ""} className="max-w-full sm:max-w-xs rounded-md" />;
  if (mt === "voice" || mt === "audio")
    return <audio controls src={url} className="max-w-[280px]" />;
  if (mt === "video" && url)
    return <video controls src={url} className="max-w-full sm:max-w-xs rounded-md" />;
  if (mt === "file" && url)
    return (
      <a href={url} target="_blank" rel="noreferrer"
        className="flex items-center gap-2 px-3 py-2 rounded-md bg-elevated border border-border text-xs text-accent hover:underline">
        <FileText size={14} /> {msg.caption || t("downloadFile")}
      </a>
    );
  return null;
}

/** Markdown 渲染（统一样式，亮暗主题自适应） */
function Markdown({ content }: { content: string }) {
  return (
    <div className="max-w-none
      [&_p]:my-1 [&_pre]:bg-elevated [&_pre]:border [&_pre]:border-border
      [&_pre]:rounded-md [&_pre]:p-3 [&_pre]:overflow-x-auto
      [&_code]:font-mono [&_code]:text-[13px]
      [&_a]:text-accent [&_a]:no-underline [&_a:hover]:underline
      [&_img]:rounded-md [&_img]:max-w-full">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

export default function Chat() {
  const { t } = useTranslation("chat");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<PendingFile[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const initialLoad = useRef(true);
  const cidSeq = useRef(0);
  const nextCid = () => `c-${++cidSeq.current}`;

  const { data: botName } = useQuery({
    queryKey: ["botName"],
    queryFn: () => chatApi.botName().then((r) => r.data.name),
  });

  useEffect(() => {
    chatApi.history("web_user", 100).then((r) => {
      if (r.data?.length) {
        setMessages(r.data.map((m: Record<string, unknown>) => ({
          role: m.role as string,
          content: m.content as string,
          timestamp: m.timestamp as string,
          id: m.id as number,
        })));
      }
      requestAnimationFrame(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
        initialLoad.current = false;
      });
    });
  }, []);

  useEffect(() => {
    const es = new EventSource("/api/chat/stream");
    es.addEventListener("reply", (e) => {
      try {
        const data = JSON.parse(e.data) as ChatMessage;
        setMessages((prev) => [...prev, { role: "assistant", content: data.content, cid: nextCid() }]);
        setSending(false);
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("media", (e) => {
      try {
        const data = JSON.parse(e.data) as ChatMessage;
        setMessages((prev) => [...prev, {
          role: "assistant",
          content: data.caption || "",
          cid: nextCid(),
          media_type: data.media_type,
          url: data.url,
          caption: data.caption,
        }]);
      } catch { /* 忽略非 JSON 帧 */ }
    });
    es.addEventListener("ping", () => {});
    es.onerror = () => { console.warn("[SSE] connection error"); };
    return () => es.close();
  }, []);

  useEffect(() => {
    if (initialLoad.current) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  /** 输入框自动增高（上限约 8 行） */
  const autoGrow = useCallback(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, []);

  const handleFileSelect = useCallback(async (files: FileList | null) => {
    if (!files) return;
    const newFiles: PendingFile[] = [];
    for (const file of Array.from(files)) {
      const type = classifyFile(file.name);
      const pf: PendingFile = { file, type, uploading: true };
      if (type === "image") {
        pf.preview = URL.createObjectURL(file);
      }
      newFiles.push(pf);
    }
    setPendingFiles((prev) => [...prev, ...newFiles]);

    for (const pf of newFiles) {
      try {
        const resp = await chatApi.upload(pf.file);
        const data = resp.data as { path: string; url: string };
        setPendingFiles((prev) =>
          prev.map((f) => f.file === pf.file ? { ...f, uploading: false, path: data.path } : f)
        );
      } catch {
        setPendingFiles((prev) =>
          prev.map((f) => f.file === pf.file ? { ...f, uploading: false } : f)
        );
      }
    }
  }, []);

  const removeFile = (idx: number) => {
    setPendingFiles((prev) => {
      const f = prev[idx];
      if (f?.preview) URL.revokeObjectURL(f.preview);
      return prev.filter((_, i) => i !== idx);
    });
  };

  const handleSend = useCallback(async () => {
    const text = input.trim();
    const uploadedPaths = pendingFiles.filter((f) => f.path).map((f) => f.path!);
    if (!text && !uploadedPaths.length) return;

    const displayParts: string[] = [];
    if (text) displayParts.push(text);
    for (const pf of pendingFiles) {
      if (pf.type === "image" && pf.preview) {
        displayParts.push(`![image](${pf.preview})`);
      } else {
        displayParts.push(`[${pf.type}: ${pf.file.name}]`);
      }
    }
    setMessages((prev) => [...prev, { role: "user", content: displayParts.join("\n"), cid: nextCid() }]);
    setInput("");
    setPendingFiles([]);
    setSending(true);
    requestAnimationFrame(autoGrow);

    try {
      await chatApi.send(text || " ", "web_user", t("user"), uploadedPaths.length ? uploadedPaths : undefined);
    } catch {
      setSending(false);
      setMessages((prev) => [...prev, { role: "system", content: t("sendFailed"), cid: nextCid() }]);
    }
  }, [input, pendingFiles, t, autoGrow]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    handleFileSelect(e.dataTransfer.files);
  }, [handleFileSelect]);

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
      handleFileSelect(dt.files);
    }
  }, [handleFileSelect]);

  return (
    <div className="flex flex-col h-full max-w-4xl mx-auto w-full">
      {/* 头部：名称 + 模型选择器 + 清空 */}
      <div className="flex items-center justify-between gap-2 mb-3 shrink-0">
        <h2 className="text-base md:text-lg font-semibold text-heading truncate">
          {botName ?? "Bot"}
        </h2>
        <div className="flex items-center gap-2 shrink-0">
          <ModelSelect modelType="chat" compact />
          <Button variant="secondary" size="sm" onClick={() => setMessages([])}>
            <Trash2 size={14} />
            <span className="hidden sm:inline">{t("clear")}</span>
          </Button>
        </div>
      </div>

      {/* 消息列表 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-1 mb-3 min-h-0">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-muted text-sm">
            {t("startConversation")}
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id ?? msg.cid ?? `${msg.role}-${msg.timestamp}`}
            className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn("max-w-[85%] sm:max-w-[80%]", msg.role === "user" ? "text-right" : "text-left")}>
              {msg.media_type && <MediaBubble msg={msg} />}
              {msg.content && (
                <div className={cn(
                  "rounded-lg px-4 py-2.5 text-sm leading-relaxed inline-block text-left",
                  msg.role === "user"
                    ? "bg-accent-subtle"
                    : msg.role === "system"
                      ? "bg-danger-subtle text-danger"
                      : "bg-secondary",
                )}>
                  {msg.role === "assistant" ? (
                    <Markdown content={msg.content} />
                  ) : (
                    <div className="whitespace-pre-wrap [&_img]:rounded-md [&_img]:max-w-full">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              )}
              {msg.timestamp && (
                <div className="text-[11px] text-muted mt-0.5 px-1">{msg.timestamp}</div>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-secondary rounded-lg px-4 py-3">
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-muted animate-pulse-subtle" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted animate-pulse-subtle [animation-delay:0.15s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-muted animate-pulse-subtle [animation-delay:0.3s]" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 待发送文件预览 */}
      {pendingFiles.length > 0 && (
        <div className="flex gap-2 py-2 overflow-x-auto shrink-0">
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
                <button onClick={() => removeFile(idx)}
                  className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-danger text-white flex items-center justify-center transition-opacity opacity-100 md:opacity-0 md:group-hover:opacity-100">
                  <X size={10} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* 输入区 */}
      <div className="border border-input rounded-lg bg-card focus-within:border-ring transition-colors shrink-0"
        onDrop={handleDrop} onDragOver={(e) => e.preventDefault()}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => { setInput(e.target.value); autoGrow(); }}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={t("placeholder")}
          rows={1}
          className="w-full resize-none bg-transparent p-3 text-sm text-foreground placeholder:text-muted outline-none max-h-[180px]"
        />
        <div className="flex items-center justify-between px-3 pb-2">
          <div className="flex items-center gap-2">
            <input ref={fileInputRef} type="file" multiple className="hidden"
              onChange={(e) => { handleFileSelect(e.target.files); e.target.value = ""; }} />
            <Button variant="ghost" size="icon" onClick={() => fileInputRef.current?.click()} title={t("attachFiles")}>
              <Paperclip size={18} />
            </Button>
          </div>
          <Button variant="primary" size="sm" onClick={handleSend}
            disabled={!input.trim() && !pendingFiles.some((f) => f.path)}>
            <Send size={15} />
            {t("send")}
          </Button>
        </div>
      </div>
    </div>
  );
}
