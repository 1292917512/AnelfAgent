import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import { audioApi, faceApi } from "./api";
import type { AudioSpeakerListResult, FacePersonListResult, IdentityListFilters } from "./types";

export function speakerListQuery(filters: IdentityListFilters) {
  const { status, keyword, page, pageSize } = filters;
  return queryOptions<AudioSpeakerListResult>({ queryKey: ["audioSpeakers", filters],
    queryFn: ({ signal }) => audioApi.speakers({ status, keyword, limit: pageSize, offset: (page - 1) * pageSize }, signal).then((result) => result.data),
    refetchInterval: 15_000, placeholderData: keepPreviousData, throwOnError: false,
  });
}

export function personListQuery(filters: IdentityListFilters) {
  const { status, keyword, page, pageSize } = filters;
  return queryOptions<FacePersonListResult>({ queryKey: ["facePersons", filters],
    queryFn: ({ signal }) => faceApi.persons({ status, keyword, limit: pageSize, offset: (page - 1) * pageSize }, signal).then((result) => result.data),
    refetchInterval: 15_000, placeholderData: keepPreviousData, throwOnError: false,
  });
}
