import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";

import {
  actionLabel,
  Avatar,
  Badge,
  Changes,
  ErrorBox,
  linkClass,
  PageHeader,
  Pager,
  rowClass,
  Spinner,
  TableCard,
  tdClass,
  thClass,
  theadClass,
} from "../components/ui";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import type { AuditEntry, Page } from "../lib/types";

const ENTITIES: Record<string, string> = {
  batch: "Партия",
  receipt: "Поступление",
  job: "Задание",
  recipe: "Рецепт",
  material: "Материал",
};

export function AuditPage() {
  const [page, setPage] = useState(1);
  const { data, error } = useQuery({
    queryKey: ["audit", page],
    queryFn: () => api.get<Page<AuditEntry>>(`/audit?page=${page}&page_size=30`),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Журнал изменений" />
      <ErrorBox error={error} />
      {!data && !error && <Spinner />}
      {data && (
        <TableCard footer={<Pager page={page} pageSize={30} total={data.total} onPage={setPage} />}>
          <thead className={theadClass}>
            <tr>
              {["Когда", "Кто", "Объект", "Действие", "Было → стало"].map((t) => (
                <th key={t} className={thClass}>
                  {t}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((e) => (
              <tr key={e.id} className={rowClass}>
                <td className={`${tdClass} whitespace-nowrap text-muted tabular-nums`}>{dateTime(e.at)}</td>
                <td className={tdClass}>
                  <span className="flex items-center gap-2.5">
                    <Avatar name={e.user_full_name} />
                    {e.user_full_name}
                  </span>
                </td>
                <td className={`${tdClass} whitespace-nowrap`}>
                  {e.entity === "batch" || e.entity === "job" ? (
                    <Link
                      className={linkClass}
                      to={`/${e.entity === "job" ? "jobs" : "batches"}/${e.entity_id}`}
                    >
                      {ENTITIES[e.entity]} #{e.entity_id}
                    </Link>
                  ) : (
                    `${ENTITIES[e.entity] ?? e.entity} #${e.entity_id}`
                  )}
                </td>
                <td className={tdClass}>
                  <Badge>{actionLabel(e.action)}</Badge>
                </td>
                <td className={tdClass}>
                  <Changes entry={e} />
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </>
  );
}
