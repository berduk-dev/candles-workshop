import { useQuery } from "@tanstack/react-query";

import { api } from "./api";
import type { Page } from "./types";

/** Сколько всего записей по фильтру. Сами записи не нужны, поэтому просим страницу из одной. */
export function useCount(path: string): number | undefined {
  const { data } = useQuery({
    queryKey: ["count", path],
    queryFn: () => api.get<Page<unknown>>(`${path}${path.includes("?") ? "&" : "?"}page_size=1`),
  });
  return data?.total;
}
