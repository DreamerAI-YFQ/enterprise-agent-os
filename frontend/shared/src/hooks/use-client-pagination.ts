import { useState, useMemo, useEffect } from "react";

export interface UseClientPaginationOptions {
  pageSize?: number;
}

export function useClientPagination<T>(
  items: T[],
  { pageSize = 20 }: UseClientPaginationOptions = {},
) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const total = items.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  useEffect(() => {
    if (page > totalPages) setPage(1);
  }, [page, totalPages]);

  const paged = useMemo(() => {
    const start = (page - 1) * pageSize;
    return items.slice(start, start + pageSize);
  }, [items, page, pageSize]);

  return {
    page,
    pageSize,
    total,
    paged,
    setPage,
    search,
    setSearch,
  };
}
