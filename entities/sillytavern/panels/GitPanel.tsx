import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  GitCommitHorizontal,
  GitPullRequest,
  FileWarning,
  History,
  RefreshCw,
} from "lucide-react";
import { apiErrorMessage, sillytavernApi } from "./api";
import { Badge, Button, LoadingBlock, Textarea, toast } from "@/components/ui";
import { Card } from "@/components/common/Card";

/** 仓库面板：当前版本 + 远端版本切换 + 拉取更新 + 提交 */
export function GitPanel() {
  const { t } = useTranslation(["sillytavern", "common"]);
  const queryClient = useQueryClient();

  const [remote, setRemote] = useState("origin");
  const [commitMessage, setCommitMessage] = useState("");

  const { data: git, isLoading: gitLoading } = useQuery({
    queryKey: ["st", "git"],
    queryFn: () => sillytavernApi.git().then((r) => r.data),
  });

  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ["st", "git-versions", remote],
    queryFn: () => sillytavernApi.gitVersions(remote).then((r) => r.data),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["st", "git"] });
    queryClient.invalidateQueries({ queryKey: ["st", "git-versions"] });
    queryClient.invalidateQueries({ queryKey: ["st", "status"] });
  };

  const pullMut = useMutation({
    mutationFn: () => sillytavernApi.gitUpdate(remote),
    onSuccess: (r) => {
      if (r.data.ok) {
        toast.success(t("sillytavern:git.pullSuccess"));
      } else {
        toast.error(`${t("sillytavern:git.pullFailed")}: ${(r.data.output || "").slice(-200)}`);
      }
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const checkoutMut = useMutation({
    mutationFn: (name: string) => sillytavernApi.gitCheckout(name, remote),
    onSuccess: (r) => {
      if (r.data.ok) {
        toast.success(t("sillytavern:git.checkoutSuccess", { branch: r.data.branch }));
      } else {
        toast.error(r.data.error || t("sillytavern:git.checkoutFailed"));
      }
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:common.requestFailed"))),
  });

  const commitMut = useMutation({
    mutationFn: () => sillytavernApi.gitCommit(commitMessage),
    onSuccess: (r) => {
      toast.success(t("sillytavern:git.commitSuccess", { commit: r.data.commit }));
      setCommitMessage("");
      invalidate();
    },
    onError: (err) => toast.error(apiErrorMessage(err, t("sillytavern:git.commitFailed"))),
  });

  if (gitLoading || !git) return <LoadingBlock label={t("common:loading")} />;

  const dirty = git.dirty_count > 0;
  const busy = pullMut.isPending || checkoutMut.isPending;

  return (
    <div className="space-y-4">
      {/* 当前版本 */}
      <Card title={t("sillytavern:git.currentVersion")}>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <div className="min-w-0 shrink-0">
            <span className="block text-xs font-medium uppercase tracking-wider text-muted">
              {t("sillytavern:git.branch")}
            </span>
            <p className="mt-1 font-mono text-sm text-heading">{git.branch || "--"}</p>
          </div>
          <div className="min-w-0 flex-1">
            <span className="block text-xs font-medium uppercase tracking-wider text-muted">
              {t("sillytavern:git.lastCommit")}
            </span>
            <p
              className="mt-1 truncate font-mono text-xs text-foreground"
              title={git.last_commit}
            >
              {git.last_commit || "--"}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3 border-t border-border pt-4">
          <label className="block">
            <span className="block text-xs font-medium text-muted">
              {t("sillytavern:git.remote")}
            </span>
            <select
              value={remote}
              onChange={(e) => setRemote(e.target.value)}
              className="mt-1 h-9 w-44 rounded-md border border-input bg-elevated px-2 text-sm text-foreground"
            >
              {(versions?.remotes ?? [{ name: remote, url: "" }]).map((r) => (
                <option key={r.name} value={r.name}>{r.name}</option>
              ))}
            </select>
          </label>
          <Button
            variant="primary"
            size="md"
            loading={pullMut.isPending}
            disabled={busy}
            onClick={() => pullMut.mutate()}
          >
            <GitPullRequest size={14} />
            {pullMut.isPending ? t("sillytavern:git.pulling") : t("sillytavern:git.pullUpdate")}
          </Button>
          <Button
            variant="secondary"
            size="md"
            disabled={busy}
            onClick={() => invalidate()}
          >
            <RefreshCw size={14} />
            {t("sillytavern:common.refresh")}
          </Button>
        </div>
      </Card>

      {/* 远端版本列表（可切换） */}
      <Card
        title={t("sillytavern:git.availableVersions")}
        actions={
          <span className="text-xs text-muted">{versions?.fetch_hint ?? ""}</span>
        }
      >
        {versionsLoading || !versions ? (
          <LoadingBlock label={t("common:loading")} />
        ) : versions.versions.length === 0 ? (
          <p className="text-sm text-muted">{t("sillytavern:git.noVersions")}</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border bg-elevated">
            {versions.versions.map((v) => (
              <li key={v.name} className="flex items-center gap-3 px-3 py-2.5">
                <History size={14} className="shrink-0 text-muted" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-heading">{v.name}</span>
                    {v.current && (
                      <Badge variant="ok">{t("sillytavern:git.currentTag")}</Badge>
                    )}
                  </div>
                  <p className="mt-0.5 truncate font-mono text-[11px] text-muted">
                    {v.commit}
                  </p>
                </div>
                {!v.current && (
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={checkoutMut.isPending && checkoutMut.variables === v.name}
                    disabled={busy || dirty}
                    title={dirty ? t("sillytavern:git.checkoutDirtyBlocked") : undefined}
                    onClick={() => checkoutMut.mutate(v.name)}
                  >
                    <Check size={13} />
                    {t("sillytavern:git.switchTo")}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {dirty && (
          <p className="mt-2 text-xs text-warn">{t("sillytavern:git.checkoutDirtyBlocked")}</p>
        )}
      </Card>

      {/* 未提交修改 */}
      <Card
        title={t("sillytavern:git.dirtyFiles", { count: git.dirty_count })}
        actions={
          !dirty ? <Badge variant="ok">{t("sillytavern:git.noDirty")}</Badge> : null
        }
      >
        {dirty ? (
          <ul className="max-h-56 overflow-auto rounded-md border border-border bg-elevated divide-y divide-border">
            {git.dirty_files.map((f) => (
              <li
                key={f}
                className="flex items-center gap-2 px-3 py-1.5 text-xs font-mono text-foreground"
              >
                <FileWarning size={12} className="shrink-0 text-warn" />
                <span className="break-all">{f}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">{t("sillytavern:git.noDirty")}</p>
        )}

        <div className="mt-4 flex flex-col gap-2">
          <Textarea
            rows={2}
            value={commitMessage}
            onChange={(e) => setCommitMessage(e.target.value)}
            placeholder={t("sillytavern:git.commitPlaceholder")}
            disabled={!dirty}
          />
          <div className="flex justify-end">
            <Button
              variant="primary"
              size="sm"
              loading={commitMut.isPending}
              disabled={!dirty || !commitMessage.trim()}
              title={!dirty ? t("sillytavern:git.commitDisabled") : undefined}
              onClick={() => commitMut.mutate()}
            >
              <GitCommitHorizontal size={14} />
              {commitMut.isPending ? t("sillytavern:git.committing") : t("sillytavern:git.commit")}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}
