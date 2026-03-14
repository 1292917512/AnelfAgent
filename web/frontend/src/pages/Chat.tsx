import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { chatApi } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Send, Loader2, Trash2, Paperclip, X, FileText, Image as ImageIcon, Music, Video } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface ChatMessage {
  role: string;
  content: string;
  timestamp?: string;
  id?: number;
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
    return <img src={url} alt={msg.caption || ""} className="max-w-xs rounded-lg" />;
  if (mt === "voice" || mt === "audio")
    return <audio controls src={url} className="max-w-[280px]" />;
  if (mt === "video" && url)
    return <video controls src={url} className="max-w-xs rounded-lg" />;
  if (mt === "file" && url)
    return (
      <a href={url} target="_blank" rel="noreferrer"
        className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[var(--bg-elevated)] border border-[var(--border)] text-xs text-[var(--accent)] hover:underline">
        <FileText size={14} /> {msg.caption || t("downloadFile")}
      </a>
    );
  return null;
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
        setMessages((prev) => [...prev, { role: "assistant", content: data.content }]);
        setSending(false);
      } catch { /* */ }
    });
    es.addEventListener("media", (e) => {
      try {
        const data = JSON.parse(e.data) as ChatMessage;
        setMessages((prev) => [...prev, {
          role: "assistant",
          content: data.caption || "",
          media_type: data.media_type,
          url: data.url,
          caption: data.caption,
        }]);
      } catch { /* */ }
    });
    es.addEventListener("ping", () => {});
    es.onerror = () => { console.warn("[SSE] connection error"); };
    return () => es.close();
  }, []);

  useEffect(() => {
    if (initialLoad.current) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

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
    setMessages((prev) => [...prev, { role: "user", content: displayParts.join("\n") }]);
    setInput("");
    setPendingFiles([]);
    setSending(true);

    try {
      await chatApi.send(text || " ", "web_user", t("user"), uploadedPaths.length ? uploadedPaths : undefined);
    } catch {
      setSending(false);
      setMessages((prev) => [...prev, { role: "system", content: t("sendFailed") }]);
    }
  }, [input, sending, pendingFiles, t]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
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
    <div className="flex flex-col h-[calc(100vh-7rem)] max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-[var(--text-strong)]">
          {botName ?? "Bot"}
        </h2>
        <button onClick={() => setMessages([])}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-[var(--radius-md)]
            border border-[var(--border)] bg-[var(--bg-elevated)] text-[var(--muted)]
            hover:bg-[var(--bg-hover)] hover:border-[var(--border-strong)] transition-all">
          <Trash2 size={14} /> {t("clear")}
        </button>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-2 mb-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-[var(--muted)] text-sm">
            {t("startConversation")}
          </div>
        )}
        {messages.map((msg) => (
          <div key={msg.id ?? `${msg.role}-${msg.timestamp}`} className={cn("flex", msg.role === "user" ? "justify-end" : "justify-start")}>
            <div className={cn("max-w-[80%]", msg.role === "user" ? "text-right" : "text-left")}>
              {msg.media_type && <MediaBubble msg={msg} />}
              {msg.content && (
                <div className={cn(
                  "rounded-[var(--radius-lg)] px-4 py-2.5 text-sm leading-relaxed inline-block text-left",
                  msg.role === "user"
                    ? "bg-[var(--accent-subtle)] border border-transparent"
                    : msg.role === "system"
                      ? "bg-[var(--danger-subtle)] text-[var(--danger)] border border-transparent"
                      : "bg-[var(--secondary)] border border-transparent",
                )}>
                  {msg.role === "assistant" ? (
                    <div className="prose prose-sm prose-invert max-w-none
                      [&_p]:my-1 [&_pre]:bg-[var(--bg-elevated)] [&_pre]:border [&_pre]:border-[var(--border)]
                      [&_pre]:rounded-[var(--radius-md)] [&_pre]:p-3
                      [&_code]:font-[var(--mono)] [&_code]:text-[13px]
                      [&_a]:text-[var(--accent)] [&_a]:no-underline [&_a:hover]:underline
                      [&_img]:rounded-lg [&_img]:max-w-full">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                  ) : (
                    <div className="whitespace-pre-wrap">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
                    </div>
                  )}
                </div>
              )}
              {msg.timestamp && (
                <div className="text-[11px] text-[var(--muted)] mt-0.5 px-1">{msg.timestamp}</div>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-[var(--secondary)] rounded-[var(--radius-lg)] px-4 py-3">
              <div className="flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--muted)] animate-[pulse-subtle_1.2s_ease-in-out_infinite]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--muted)] animate-[pulse-subtle_1.2s_ease-in-out_infinite_0.15s]" />
                <span className="w-1.5 h-1.5 rounded-full bg-[var(--muted)] animate-[pulse-subtle_1.2s_ease-in-out_infinite_0.3s]" />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Pending files preview */}
      {pendingFiles.length > 0 && (
        <div className="flex gap-2 px-3 py-2 border-t border-[var(--border)] overflow-x-auto">
          {pendingFiles.map((pf, idx) => {
            const Icon = FILE_TYPE_ICONS[pf.type] || FileText;
            return (
              <div key={`${pf.file.name}-${idx}`} className="relative flex-shrink-0 group">
                {pf.preview ? (
                  <img src={pf.preview} alt="" className="w-16 h-16 rounded-lg object-cover border border-[var(--border)]" />
                ) : (
                  <div className="w-16 h-16 rounded-lg border border-[var(--border)] bg-[var(--bg-elevated)] flex flex-col items-center justify-center gap-1">
                    <Icon size={16} className="text-[var(--muted)]" />
                    <span className="text-[9px] text-[var(--muted)] truncate max-w-[56px]">{pf.file.name}</span>
                  </div>
                )}
                {pf.uploading && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/40 rounded-lg">
                    <Loader2 size={16} className="text-white animate-spin" />
                  </div>
                )}
                <button onClick={() => removeFile(idx)}
                  className="absolute -top-1.5 -right-1.5 w-4 h-4 rounded-full bg-[var(--danger)] text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                  <X size={10} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {/* Input area */}
      <div className="border border-[var(--input)] rounded-[var(--radius-lg)] bg-[var(--card)] focus-within:border-[var(--ring)] transition-colors"
        onDrop={handleDrop} onDragOver={(e) => e.preventDefault()}>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
          placeholder={t("placeholder")}
          rows={3}
          className="w-full resize-none bg-transparent p-3 text-sm text-[var(--text)] placeholder:text-[var(--muted)] outline-none"
        />
        <div className="flex items-center justify-between px-3 pb-2">
          <div className="flex items-center gap-2">
            <input ref={fileInputRef} type="file" multiple className="hidden"
              onChange={(e) => { handleFileSelect(e.target.files); e.target.value = ""; }} />
            <button onClick={() => fileInputRef.current?.click()}
              className="p-1.5 rounded-[var(--radius-md)] text-[var(--muted)] hover:text-[var(--text)] hover:bg-[var(--bg-hover)] transition-colors"
              title={t("attachFiles")}>
              <Paperclip size={18} />
            </button>
          </div>
          <button onClick={handleSend}
            disabled={!input.trim() && !pendingFiles.some((f) => f.path)}
            className={cn(
              "flex items-center gap-2 px-4 py-1.5 rounded-[var(--radius-md)] text-sm font-medium transition-all",
              "bg-[var(--accent)] text-[var(--primary-foreground)]",
              "hover:bg-[var(--accent-hover)] hover:-translate-y-px hover:shadow-[var(--shadow-sm)]",
              "disabled:opacity-50 disabled:cursor-not-allowed disabled:translate-y-0",
            )}>
            <Send size={16} />
            {t("send")}
          </button>
        </div>
      </div>
    </div>
  );
}
