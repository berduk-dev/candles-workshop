import { keepPreviousData, useQuery } from "@tanstack/react-query";
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
  PeriodPicker,
  rowClass,
  SortableTh,
  Spinner,
  TableCard,
  tdClass,
  thClass,
  theadClass,
} from "../components/ui";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import { DEFAULT_PERIOD, readPeriod, withRange } from "../lib/period";
import type { AuditEntry, Page } from "../lib/types";
import { useUrlParams } from "../lib/url";

const ENTITIES: Record<string, string> = {
  batch: "Партия",
  receipt: "Поступление",
  job: "Задание",
  recipe: "Рецепт",
  material: "Материал",
};

const COLUMNS = [
  ["at", "Когда"],
  ["user", "Кто"],
  ["entity", "Объект"],
  ["action", "Действие"],
  [null, "Было → стало"],
] as const;

export function AuditPage() {
  const [params, update] = useUrlParams();
  const sort = params.get("sort") ?? "-at";
  const page = Number(params.get("page") ?? 1);
  const { period, range } = readPeriod(params, "date_from", "date_to");
  const query = withRange(new URLSearchParams(), range, "date_from", "date_to");
  query.set("sort", sort);
  query.set("page", String(page));
  query.set("page_size", "30");
  const { data, error } = useQuery({
    queryKey: ["audit", query.toString()],
    queryFn: () => api.get<Page<AuditEntry>>(`/audit?${query}`),
    placeholderData: keepPreviousData,
  });
  return (
    <>
      <PageHeader title="Журнал изменений" />
      <div className="mb-3">
        <PeriodPicker
          label="Когда изменено"
          value={period}
          onChange={(next) => update({ period: next === DEFAULT_PERIOD ? "" : next })}
        />
      </div>
      <ErrorBox error={error} />
      {!data && !error && <Spinner />}
      {data && (
        <TableCard
          footer={
            data.total === 0 ? (
              <p className="px-4 py-6 text-center text-muted">За выбранный период изменений нет.</p>
            ) : (
              <Pager
                page={page}
                pageSize={30}
                total={data.total}
                onPage={(p) => update({ page: String(p) })}
              />
            )
          }
        >
          <thead className={theadClass}>
            <tr>
              {COLUMNS.map(([field, title]) =>
                field ? (
                  <SortableTh
                    key={title}
                    field={field}
                    title={title}
                    sort={sort}
                    onSort={(next) => update({ sort: next })}
                  />
                ) : (
                  <th key={title} className={thClass}>
                    {title}
                  </th>
                ),
              )}
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
