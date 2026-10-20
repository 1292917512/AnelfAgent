import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { AxiosHeaders, type AxiosResponse } from "axios";
import { expect, it, vi } from "vitest";
import { audioApi } from "./api";
import { speakerListQuery } from "./identity-queries";
import { useIdentityFilters } from "@/components/identity/useIdentityFilters";
import type { AudioSpeakerListResult } from "./types";

it("debounces searches, cancels abandoned reads and resets pagination without replacing newer results", async () => {
  vi.useFakeTimers();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const replies: ((value: AxiosResponse<AudioSpeakerListResult>) => void)[] = [];
  const read = vi.spyOn(audioApi, "speakers").mockImplementation(() => new Promise((resolve) => { replies.push(resolve); }));
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  const { result, unmount } = renderHook(() => {
    const filter = useIdentityFilters();
    const query = useQuery(speakerListQuery(filter.filters));
    return { filter, query };
  }, { wrapper });
  const response = (total: number): AxiosResponse<AudioSpeakerListResult> => ({ data: { items: [], total, limit: 50, offset: 0 },
    status: 200, statusText: "OK", headers: {}, config: { headers: new AxiosHeaders() } });
  try {
    expect(read).toHaveBeenCalledOnce();
    act(() => { result.current.filter.setKeyword("old"); result.current.filter.setKeyword("latest"); });
    await act(async () => { await vi.advanceTimersByTimeAsync(249); });
    expect(read).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(read).toHaveBeenCalledTimes(2);
    expect(read.mock.calls[0]?.[1]?.aborted).toBe(true);
    expect(read.mock.calls[1]?.[0]).toMatchObject({ keyword: "latest", offset: 0, limit: 50 });
    await act(async () => { replies[1]?.(response(111)); await vi.advanceTimersByTimeAsync(1); });
    await act(async () => { replies[0]?.(response(999)); await vi.advanceTimersByTimeAsync(1); });
    expect(result.current.query.data?.total).toBe(111);
    act(() => { result.current.filter.setPage(2); });
    expect(read.mock.calls[2]?.[0]).toMatchObject({ offset: 50 });
    expect(result.current.query.data?.total).toBe(111);
    act(() => { result.current.filter.setKeyword("new filter"); });
    await act(async () => { await vi.advanceTimersByTimeAsync(250); });
    expect(read.mock.calls[3]?.[0]).toMatchObject({ keyword: "new filter", offset: 0 });
    expect(result.current.filter.page).toBe(1);
  } finally { unmount(); client.clear(); vi.useRealTimers(); }
});
