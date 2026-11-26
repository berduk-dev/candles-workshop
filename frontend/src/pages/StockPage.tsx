import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import {
  Badge,
  ErrorBox,
  linkClass,
  PageHeader,
  rowClass,
  SortableTh,
  Spinner,
  StatStrip,
  TableCard,
  tdClass,
  theadClass,
} from "../components/ui";
import { api } from "../lib/api";
import { isZero, num } from "../lib/format";
import type { MaterialStock } from "../lib/types";
import { useUrlParams } from "../lib/url";

const COLUMNS = [
  ["material", "Материал"],
  ["qty_on_hand", "Остаток"],
  ["qty_reserved", "В резерве"],
  ["qty_free", "Свободно"],
  ["reserved_share", "Занято резервом"],
  ["qty_on_check", "На проверке"],
] as const;

/** Остатки — состояние склада на сейчас, поэтому периода здесь нет, только сортировка. */
export function StockPage() {
  const [params, update] = useUrlParams();
  const sort = params.get("sort") ?? "material";
  const { data, error } = useQuery({
    queryKey: ["materials", sort],
    queryFn: () => api.get<MaterialStock[]>(`/materials?sort=${sort}`),
    placeholderData: keepPreviousData,
  });
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  const empty = data.filter((m) => isZero(m.qty_free)).length;
  return (
    <>
      <PageHeader title="Остатки материалов" />
      <StatStrip
        items={[
          { label: "Материалов", value: data.length },
          { label: "Есть свободное", value: data.length - empty },
          { label: "Свободного нет", value: empty },
          { label: "Ждут проверки", value: data.filter((m) => !isZero(m.qty_on_check)).length },
        ]}
      />
      <TableCard>
        <thead className={theadClass}>
          <tr>
            {COLUMNS.map(([field, title]) => (
              <SortableTh
                key={field}
                field={field}
                title={title}
                sort={sort}
                onSort={(next) => update({ sort: next })}
              />
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((m) => (
            <tr key={m.id} className={rowClass}>
              <td className={tdClass}>
                <Link className={linkClass} to={`/batches?material=${m.code}&period=all`}>
                  {m.name}
                </Link>
                <div className="mt-0.5 text-xs text-muted">{m.code}</div>
              </td>
              <td className={`${tdClass} whitespace-nowrap tabular-nums`}>
                {num(m.qty_on_hand)} {m.unit}
              </td>
              <td className={`${tdClass} whitespace-nowrap text-muted tabular-nums`}>
                {isZero(m.qty_reserved) ? "—" : `${num(m.qty_reserved)} ${m.unit}`}
              </td>
              <td className={`${tdClass} font-semibold whitespace-nowrap tabular-nums`}>
                {isZero(m.qty_free) ? (
                  <Badge tone="bad" dot>
                    Нет
                  </Badge>
                ) : (
                  `${num(m.qty_free)} ${m.unit}`
                )}
              </td>
              <td className={tdClass}>
                <ReservedBar material={m} />
              </td>
              <td className={tdClass}>
                {isZero(m.qty_on_check) ? (
                  <span className="text-muted">—</span>
                ) : (
                  <Badge tone="warn">
                    {num(m.qty_on_check)} {m.unit}
                  </Badge>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </TableCard>
    </>
  );
}

/** Доля остатка под резервом. Number здесь только для ширины полоски, в учёт не попадает. */
function ReservedBar({ material }: { material: MaterialStock }) {
  if (isZero(material.qty_on_hand)) return <span className="text-muted">—</span>;
  const share = Math.round((Number(material.qty_reserved) / Number(material.qty_on_hand)) * 100);
  return (
    <div className="flex items-center gap-3">
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-line/70">
        <div
          className={`h-full rounded-full ${share >= 100 ? "bg-bad" : "bg-ink"}`}
          style={{ width: `${share}%` }}
        />
      </div>
      <span className="w-9 text-xs text-muted tabular-nums">{share}%</span>
    </div>
  );
}
