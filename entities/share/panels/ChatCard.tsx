import type { JsonObject } from "@/lib/types/json";
import type { ChatShareInfo } from "./chat-types";
import { ShareCard } from "./ShareCard";

function parseShare(payload: JsonObject): ChatShareInfo {
  const { token, url, share_type, media_kind, file_name } = payload;
  if (typeof token !== "string" || !/^[a-zA-Z0-9_-]+$/.test(token) || typeof url !== "string" || typeof file_name !== "string"
    || (share_type !== "file" && share_type !== "media" && share_type !== "link")
    || (media_kind !== "image" && media_kind !== "video" && media_kind !== "audio" && media_kind !== "pdf" && media_kind !== "html" && media_kind !== "")) {
    throw new Error("Invalid share card payload");
  }
  return { token, url, share_type, media_kind, file_name,
    download_url: typeof payload.download_url === "string" ? payload.download_url : undefined,
    target_url: typeof payload.target_url === "string" ? payload.target_url : undefined,
    description: typeof payload.description === "string" ? payload.description : undefined,
    file_size: typeof payload.file_size === "number" ? payload.file_size : undefined };
}

export default function ChatCard({ payload }: { payload: JsonObject }) {
  return <ShareCard share={parseShare(payload)} />;
}
