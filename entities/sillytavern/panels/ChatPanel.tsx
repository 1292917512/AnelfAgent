import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Send } from "lucide-react";
import { apiErrorMessage, sillytavernApi } from "./api";
import type { StChatMessage } from "./types";
import { Button, LoadingBlock, Select, Textarea, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";

/** AI 与酒馆角色的对话面板：左侧角色选择，右侧消息流 + 输入框。 */
export function ChatPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();
  const bottomRef = useRef<HTMLDivElement>(null);

  const [avatar, setAvatar] = useState("");
  const [message, setMessage] = useState("");

  const { data: status } = useQuery({
    queryKey: ["st", "status"],
    queryFn: () => sillytavernApi.status().then((r) => r.data),
    refetchInterval: 15_000,
  });
  const running = Boolean(status?.running);

  const { data: charsData } = useQuery({
    queryKey: ["st", "characters"],
    queryFn: () => sillytavernApi.characters().then((r) => r.data),
    enabled: running,
    retry: false,
  });
  const characters = charsData?.characters ?? [];

  const currentChar = characters.find((c) => c.avatar === avatar);
  const chatFile = currentChar
    ? `Anelf - ${new Date().toISOString().slice(0, 10)}`
    : "";

  // 当前聊天的消息流（角色选定后轮询）
  const { data: chatData, isLoading: chatLoading } = useQuery({
    queryKey: ["st", "chat-content", avatar, chatFile],
    queryFn: () => sillytavernApi.chatContent(avatar, chatFile).then((r) => r.data),
    enabled: running && Boolean(avatar),
    retry: false,
  });
  const messages: StChatMessage[] = (chatData?.messages ?? []).filter(
    (m) => m.mes && !m.is_system && m.name !== undefined,
  );

  const sendMut = useMutation({
    mutationFn: () => sillytavernApi.chatSend(avatar, message.trim(), chatFile),
    onSuccess: () => {
      setMessage("");
      queryClient.invalidateQueries({ queryKey: ["st", "chat-content", avatar, chatFile] });
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, sendMut.isPending]);

  if (!running) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 py-20 text-muted">
        <MessageSquare size={40} className="opacity-40" />
        <p className="text-sm">{t("sillytavern:chat.notRunningHint")}</p>
      </Card>
    );
  }

  return (
    <Card
      title={t("sillytavern:chat.title")}
      subtitle={t("sillytavern:chat.subtitle")}
    >
      <div className="flex flex-col gap-4">
        {/* 角色选择 */}
        <label className="block max-w-sm">
          <span className="text-xs font-medium text-muted">
            {t("sillytavern:chat.pickChar")}
          </span>
          <Select
            value={avatar}
            onChange={(e) => setAvatar(e.target.value)}
            className="mt-1 w-full"
          >
            <option value="">—</option>
            {characters.map((c) => (
              <option key={c.avatar} value={c.avatar}>
                {c.name}
              </option>
            ))}
          </Select>
        </label>

        {/* 消息流 */}
        <div className="min-h-64 max-h-96 overflow-auto rounded-md border border-border bg-elevated p-3">
          {!avatar ? (
            <p className="py-16 text-center text-sm text-muted">
              {t("sillytavern:chat.empty")}
            </p>
          ) : chatLoading ? (
            <LoadingBlock label={t("common:loading")} />
          ) : messages.length === 0 ? (
            <p className="py-16 text-center text-sm text-muted">
              {t("sillytavern:chat.empty")}
            </p>
          ) : (
            <div className="space-y-3">
              {messages.map((m, i) => (
                <div
                  key={i}
                  className={
                    m.is_user
                      ? "flex justify-end"
                      : "flex justify-start"
                  }
                >
                  <div
                    className={
                      "max-w-[80%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words " +
                      (m.is_user
                        ? "bg-accent/20 text-heading"
                        : "bg-card border border-border text-foreground")
                    }
                  >
                    <div className="mb-0.5 text-[11px] font-medium text-muted">
                      {m.name}
                    </div>
                    {m.mes}
                  </div>
                </div>
              ))}
              {sendMut.isPending && (
                <div className="flex justify-start">
                  <div className="rounded-lg border border-border bg-card px-3 py-2 text-sm text-muted">
                    {t("sillytavern:chat.sending")}
                  </div>
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          )}
        </div>

        {/* 输入区 */}
        <div className="flex items-end gap-2">
          <Textarea
            rows={2}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder={t("sillytavern:chat.message")}
            disabled={!avatar || sendMut.isPending}
            className="flex-1"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (avatar && message.trim()) sendMut.mutate();
              }
            }}
          />
          <Button
            variant="primary"
            size="md"
            loading={sendMut.isPending}
            disabled={!avatar || !message.trim()}
            onClick={() => sendMut.mutate()}
          >
            <Send size={14} />
            {t("sillytavern:chat.send")}
          </Button>
        </div>
      </div>
    </Card>
  );
}
