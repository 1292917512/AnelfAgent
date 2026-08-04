import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { shareApi, workspaceApi } from "@/lib/api";
import { Card } from "@/components/common/Card";
import { Copy, Download, ExternalLink, FileText, FolderOpen, Globe, Image as ImageIcon, Link2, Plus, ChevronRight, ChevronDown } from "lucide-react";
import { toast } from "@/stores/toast-store";
import type { CreateShareRequest, ShareLink, ShareType, WorkspaceNode } from "@/lib/types";

const EXPIRES_OPTIONS = ["1h", "6h", "24h", "7d", "30d", "never"] as const;

const SHARE_TYPES: Array<{ value: ShareType; icon: typeof FileText }> = [
  { value: "file", icon: Download },
  { value: "media", icon: ImageIcon },
  { value: "link", icon: Globe },
];

function FileTreeNode({
  node,
  depth,
  onSelect,
}: {
  node: WorkspaceNode;
  depth: number;
  onSelect: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);
  const isDir = node.type === "dir";

  const { data: children } = useQuery({
    queryKey: ["workspaceTree", node.path],
    queryFn: () => workspaceApi.tree(node.path, 1).then((r) => r.data.children),
    enabled: isDir && expanded && (!node.children || node.children.length === 0),
  });

  const items = node.children?.length ? node.children : children;

  return (
    <div>
      <div
        className={`flex items-center gap-1.5 py-1 px-2 rounded-md cursor-pointer hover:bg-hover transition-colors ${
          !isDir ? "text-foreground" : "text-heading font-medium"
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={() => {
          if (isDir) {
            setExpanded(!expanded);
          } else {
            onSelect(node.path);
          }
        }}
      >
        {isDir ? (
          <>
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <FolderOpen size={14} className="text-accent" />
          </>
        ) : (
          <>
            <span className="w-3.5" />
            <FileText size={14} className="text-muted" />
          </>
        )}
        <span className="text-sm truncate">{node.name}</span>
        {!isDir && node.size !== undefined && (
          <span className="text-xs text-muted ml-auto">
            {node.size < 1024 ? `${node.size}B` : node.size < 1024 * 1024 ? `${(node.size / 1024).toFixed(1)}K` : `${(node.size / 1024 / 1024).toFixed(1)}M`}
          </span>
        )}
      </div>
      {isDir && expanded && items && (
        <div>
          {items.map((child) => (
            <FileTreeNode key={child.path} node={child} depth={depth + 1} onSelect={onSelect} />
          ))}
        </div>
      )}
    </div>
  );
}

export function CreateDialog() {
  const { t } = useTranslation("share");
  const queryClient = useQueryClient();
  const [shareType, setShareType] = useState<ShareType>("file");
  const [path, setPath] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [description, setDescription] = useState("");
  const [expiresIn, setExpiresIn] = useState<string>("24h");
  const [maxDownloads, setMaxDownloads] = useState(0);
  const [createdLink, setCreatedLink] = useState<ShareLink | null>(null);
  const [showBrowser, setShowBrowser] = useState(false);

  const isLink = shareType === "link";

  const { data: treeData } = useQuery({
    queryKey: ["workspaceTree", ""],
    queryFn: () => workspaceApi.tree("", 2).then((r) => r.data.children),
    enabled: showBrowser,
  });

  const createMutation = useMutation({
    mutationFn: (data: CreateShareRequest) => shareApi.create(data),
    onSuccess: (res) => {
      const link = res.data;
      setCreatedLink(link);
      toast.success(t("messages.createSuccess"));
      queryClient.invalidateQueries({ queryKey: ["shareLinks"] });
      queryClient.invalidateQueries({ queryKey: ["shareStats"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(msg || t("messages.createFailed"));
    },
  });

  const handleCreate = () => {
    if (isLink) {
      if (!targetUrl.trim()) {
        toast.error(t("messages.targetUrlRequired"));
        return;
      }
    } else if (!path.trim()) {
      toast.error(t("messages.fileRequired"));
      return;
    }
    createMutation.mutate({
      share_type: shareType,
      path: isLink ? "" : path.trim(),
      target_url: isLink ? targetUrl.trim() : "",
      description: description.trim(),
      expires_in: expiresIn,
      max_downloads: maxDownloads,
    });
  };

  const copyUrl = () => {
    if (!createdLink) return;
    const url = createdLink.url || `${window.location.origin}/api/entity/share/v/${createdLink.token}`;
    navigator.clipboard.writeText(url).then(
      () => toast.success(t("messages.copySuccess")),
      () => toast.error(t("messages.copyFailed")),
    );
  };

  const handleFileSelect = (selectedPath: string) => {
    setPath(selectedPath);
    setShowBrowser(false);
    toast.success(`${t("fields.filePath")}: ${selectedPath}`);
  };

  return (
    <div className="space-y-4">
      <Card title={t("tabs.create")} subtitle={t("messages.createSuccess")}>
        <div className="space-y-4">
          {/* 分享类型 */}
          <div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {SHARE_TYPES.map(({ value, icon: Icon }) => (
                <button
                  key={value}
                  onClick={() => setShareType(value)}
                  className={`flex flex-col items-start gap-1 px-3 py-2.5 rounded-md border text-left transition-all ${
                    shareType === value
                      ? "border-accent bg-accent-subtle"
                      : "border-border bg-elevated hover:bg-hover"
                  }`}
                >
                  <span className={`flex items-center gap-1.5 text-sm font-medium ${shareType === value ? "text-accent" : "text-heading"}`}>
                    <Icon size={15} />
                    {t(`types.${value}.name`)}
                  </span>
                  <span className="text-[11px] text-muted leading-snug">{t(`types.${value}.desc`)}</span>
                </button>
              ))}
            </div>
          </div>

          {/* 链接类型：目标网址 */}
          {isLink ? (
            <div>
              <label className="block text-sm font-medium text-heading mb-2">
                {t("fields.targetUrl")}
              </label>
              <input
                type="text"
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="http://127.0.0.1:8080"
                className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
              />
              <p className="text-xs text-muted mt-1">{t("targetUrlHint")}</p>
            </div>
          ) : (
            <>
              {/* 文件路径 */}
              <div>
                <label className="block text-sm font-medium text-heading mb-2">
                  {t("fields.filePath")}
                </label>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={path}
                    onChange={(e) => setPath(e.target.value)}
                    placeholder="uploads/example.pdf"
                    className="flex-1 px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent"
                  />
                  <button
                    onClick={() => setShowBrowser(!showBrowser)}
                    className="flex items-center gap-1.5 px-3 py-2 text-sm rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all"
                  >
                    <FolderOpen size={16} />
                    {t("actions.browse")}
                  </button>
                </div>
                <p className="text-xs text-muted mt-1">
                  {t("fields.filePath")} — {t("filePathHint")}
                </p>
              </div>

              {/* 文件浏览器 */}
              {showBrowser && (
                <div className="border border-border rounded-md bg-elevated max-h-64 overflow-y-auto">
                  {treeData ? (
                    treeData.length > 0 ? (
                      treeData.map((node) => (
                        <FileTreeNode key={node.path} node={node} depth={0} onSelect={handleFileSelect} />
                      ))
                    ) : (
                      <div className="p-4 text-sm text-muted text-center">{t("workspaceEmpty")}</div>
                    )
                  ) : (
                    <div className="p-4 text-sm text-muted text-center">{t("common:loading")}</div>
                  )}
                </div>
              )}
            </>
          )}

          {/* 描述 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("fields.description")}
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("fields.description") + "..."}
              rows={2}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground placeholder:text-muted focus:outline-none focus:ring-1 focus:ring-accent resize-none"
            />
          </div>

          {/* 过期策略 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("fields.expiresAt")}
            </label>
            <select
              value={expiresIn}
              onChange={(e) => setExpiresIn(e.target.value)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            >
              {EXPIRES_OPTIONS.map((opt) => (
                <option key={opt} value={opt}>
                  {t(`expires.${opt}`)}
                </option>
              ))}
            </select>
          </div>

          {/* 访问上限 */}
          <div>
            <label className="block text-sm font-medium text-heading mb-2">
              {t("fields.maxDownloads")}
            </label>
            <input
              type="number"
              min={0}
              value={maxDownloads}
              onChange={(e) => setMaxDownloads(parseInt(e.target.value, 10) || 0)}
              className="w-full px-3 py-2 text-sm rounded-md border border-border bg-elevated text-foreground focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <p className="text-xs text-muted mt-1">0 = {t("unlimited")}</p>
          </div>

          {/* 创建按钮 */}
          <button
            onClick={handleCreate}
            disabled={createMutation.isPending || (isLink ? !targetUrl.trim() : !path.trim())}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium rounded-md bg-accent text-white hover:bg-accent-hover transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Plus size={16} />
            {createMutation.isPending ? t("common:loading") : t("actions.create")}
          </button>
        </div>
      </Card>

      {/* 创建成功展示 */}
      {createdLink && (
        <Card title={t("messages.createSuccess")}>
          <div className="space-y-3">
            <div className="flex items-center gap-3 p-3 rounded-md bg-elevated border border-border">
              <Link2 size={18} className="flex-shrink-0 text-accent" />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium text-heading truncate">
                  {createdLink.file_name}
                </div>
                <div className="text-xs text-muted truncate">
                  {createdLink.url || `/api/entity/share/v/${createdLink.token}`}
                </div>
              </div>
              {createdLink.share_type !== "file" && (
                <a
                  href={createdLink.url || `/api/entity/share/v/${createdLink.token}`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all flex-shrink-0"
                >
                  <ExternalLink size={14} /> {t("actions.openPreview")}
                </a>
              )}
              <button
                onClick={copyUrl}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-elevated text-muted hover:bg-hover transition-all flex-shrink-0"
              >
                <Copy size={14} /> {t("actions.copy")}
              </button>
            </div>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <span className="text-muted">{t("fields.token")}:</span>
                <span className="ml-2 font-mono text-xs">{createdLink.token}</span>
              </div>
              <div>
                <span className="text-muted">{t("fields.expiresAt")}:</span>
                <span className="ml-2">
                  {createdLink.expires_at
                    ? new Date(createdLink.expires_at).toLocaleString()
                    : t("expires.never")}
                </span>
              </div>
              <div>
                <span className="text-muted">{t("fields.maxDownloads")}:</span>
                <span className="ml-2">
                  {createdLink.max_downloads > 0 ? createdLink.max_downloads : t("unlimited")}
                </span>
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
