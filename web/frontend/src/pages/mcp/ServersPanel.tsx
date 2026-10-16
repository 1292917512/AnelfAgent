import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DndContext, closestCenter, KeyboardSensor, PointerSensor, useSensor, useSensors, type DragEndEvent } from "@dnd-kit/core";
import { arrayMove, SortableContext, sortableKeyboardCoordinates, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { Plug, Plus, Search } from "lucide-react";
import { apiErrorMessage, mcpApi } from "@/lib/api";
import type { MCPServer } from "@/lib/types";
import { Button, ConfirmDialog, EmptyState, Input, toast } from "@/components/ui";
import { AsyncState, QueryError } from "@/components/common/AsyncState";
import { ServerCard } from "./ServerCard";
import { ServerFormModal } from "./ServerFormModal";
import type { EditingServer } from "./types";

export function ServersPanel() {
  const { t } = useTranslation("mcp");
  const client = useQueryClient();
  const [search, setSearch] = useState("");
  const [form, setForm] = useState<EditingServer | null | undefined>();
  const [deleting, setDeleting] = useState<MCPServer | null>(null);
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }));
  const refresh = () => Promise.all([
    client.invalidateQueries({ queryKey: ["mcpServers"] }), client.invalidateQueries({ queryKey: ["mcpConfig"] }),
  ]);
  const reorder = useMutation({
    mutationFn: (names: string[]) => mcpApi.reorder(names),
    onMutate: async (names) => {
      await client.cancelQueries({ queryKey: ["mcpServers"] });
      const previous = client.getQueryData<MCPServer[]>(["mcpServers"]);
      if (previous) {
        const byName = new Map(previous.map((server) => [server.name, server]));
        client.setQueryData(["mcpServers"], names.flatMap((name) => byName.get(name) ?? []));
      }
      return { previous };
    },
    onError: (error, _names, context) => {
      if (context?.previous) client.setQueryData(["mcpServers"], context.previous);
      toast.error(apiErrorMessage(error, t("toast.requestFailed")));
    },
    onSuccess: () => toast.success(t("reorder.saved")),
    onSettled: refresh,
  });
  const query = useQuery({ queryKey: ["mcpServers"], queryFn: ({ signal }) => mcpApi.list(signal).then((r) => r.data),
    refetchInterval: reorder.isPending ? false : 5000, enabled: !reorder.isPending, throwOnError: false });
  const servers = query.data ?? [];
  const { data: oauthStatus = {} } = useQuery({ queryKey: ["mcpOauthStatus"], queryFn: () => mcpApi.oauthStatus().then((r) => r.data),
    refetchInterval: 5000, enabled: servers.some((server) => server.transport !== "stdio"), throwOnError: false });
  const remove = useMutation({ mutationFn: (name: string) => mcpApi.remove(name),
    onSuccess: (_result, name) => { void refresh(); setDeleting(null); toast.success(t("toast.serverDeleted", { name })); },
    onError: (error) => toast.error(apiErrorMessage(error, t("toast.requestFailed"))),
  });
  const edit = useMutation({ mutationFn: (server: MCPServer) => mcpApi.get(server.name).then((r) => ({ name: server.name, config: r.data })),
    onSuccess: setForm, onError: (error) => toast.error(apiErrorMessage(error, t("toast.requestFailed"))),
  });
  const busy = reorder.isPending || remove.isPending || edit.isPending;
  const filtered = servers.filter((server) => `${server.name} ${server.url} ${server.transport}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const sortable = !busy && !search.trim();
  const move = (from: number, to: number) => {
    if (!sortable || from === to || from < 0 || to < 0 || to >= servers.length) return;
    reorder.mutate(arrayMove(servers, from, to).map((server) => server.name));
  };
  const drop = ({ active, over }: DragEndEvent) => {
    if (over) move(servers.findIndex((server) => server.name === active.id), servers.findIndex((server) => server.name === over.id));
  };
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="space-y-1">
        <p className="text-sm font-medium text-heading">{t("summary", { total: servers.length, connected: servers.filter((server) => server.connected).length, tools: servers.reduce((sum, server) => sum + server.tool_count, 0) })}</p>
        <p role="status" className="text-xs text-muted">{t(reorder.isPending ? "reorder.saving" : search.trim() ? "reorder.filtered" : "reorder.hint")}</p>
      </div>
      <div className="flex w-full items-center gap-2 sm:w-auto">
        <div className="relative min-w-0 flex-1 sm:w-64"><Search size={15} className="absolute left-3 top-3 text-muted" />
          <Input className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("searchServers")} placeholder={t("searchServers")} />
        </div>
        <Button variant="primary" size="sm" disabled={busy} onClick={() => setForm(null)}><Plus size={15} />{t("addServer")}</Button>
      </div>
    </div>
    {query.data && query.error && <QueryError compact error={query.error} retry={() => void query.refetch()} />}
    <AsyncState pending={query.isPending} error={query.data ? undefined : query.error} retry={() => void query.refetch()}>
      {!servers.length ? <EmptyState icon={Plug} title={t("noServers")} description={t("noServersHint")} />
        : !filtered.length ? <p className="py-12 text-center text-sm text-muted">{t("noMatches")}</p>
        : <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={drop}>
          <SortableContext items={filtered.map((server) => server.name)} strategy={verticalListSortingStrategy}>
            <div className="space-y-2">{filtered.map((server) => <ServerCard key={server.name} server={server} oauth={oauthStatus[server.name]}
              position={servers.indexOf(server)} total={servers.length} sortable={sortable} disabled={busy}
              onMove={(offset) => move(servers.indexOf(server), servers.indexOf(server) + offset)} onEdit={(target) => edit.mutate(target)} onDelete={setDeleting} />)}</div>
          </SortableContext>
        </DndContext>}
    </AsyncState>
    {form !== undefined && <ServerFormModal editing={form} onClose={() => setForm(undefined)} />}
    <ConfirmDialog open={!!deleting} onClose={() => { if (!remove.isPending) setDeleting(null); }}
      onConfirm={() => deleting && remove.mutate(deleting.name)} title={t("deleteTitle")}
      message={deleting ? t("deleteMessage", { name: deleting.name }) : ""} danger loading={remove.isPending} />
  </div>;
}
