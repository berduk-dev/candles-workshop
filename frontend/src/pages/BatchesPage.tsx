import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronsUpDown, Download, PackageSearch, Plus, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";

import {
  Badge,
  BATCH_TONE,
  Button,
  buttonClass,
  Empty,
  ErrorBox,
  inputClass,
  linkClass,
  PageHeader,
  Pager,
  rowClass,
  TableCard,
  theadClass,
} from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { useCount } from "../lib/counts";
import { day, num, rub } from "../lib/format";
import type { Batch, Page } from "../lib/types";

const PAGE_SIZE = 15;

const COLUMNS = [
  ["number", "Номер"],
  ["material", "Материал"],
  ["supplier", "Поставщик"],
  ["received_at", "Дата"],
  ["qty_initial", "Поступило"],
  ["qty_on_hand", "Остаток"],
  ["qty_free", "Свободно"],
  ["price", "Цена"],
  ["currency", "Валюта"],
  ["unit_cost_rub", "Себестоимость"],
  ["status", "Статус"],
] as const;

/** Фильтры, сортировка и страница живут в адресе: ссылку можно отправить коллеге, «Назад» работает. */
export function BatchesPage() {
  const [params, setParams] = useSearchParams();
  const canReceive = useCan("receipt_create");
  const sort = params.get("sort") ?? "-received_at";
  const page = Number(params.get("page") ?? 1);
  const [resets, setResets] = useState(0); // новый key — поля фильтров перечитывают адрес

  const query = new URLSearchParams(params);
  query.set("sort", sort);
  query.set("page", String(page));
  query.set("page_size", String(PAGE_SIZE));

  const { data, error } = useQuery({
    queryKey: ["batches", query.toString()],
    queryFn: () => api.get<Page<Batch>>(`/batches?${query}`),
    placeholderData: keepPreviousData,
  });

  function update(changes: Record<string, string>) {
    // Основа — адрес из окна, а не из рендера. Поля с паузой вызывают update позже, из старого рендера,
    // а setParams(fn) в react-router отдаёт функции адрес того же старого рендера (очереди, как
    // у setState, у него нет). Два фильтра подряд — «от» и сразу «до» — затирали друг друга.
    const next = new URLSearchParams(window.location.search);
    if (!("page" in changes)) next.delete("page"); // новый фильтр — снова первая страница
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setParams(next, { replace: true });
  }

  const exportQuery = new URLSearchParams(query);
  exportQuery.delete("page");
  exportQuery.delete("page_size");

  const filtered = [...params.keys()].some((key) => key !== "sort" && key !== "page");

  return (
    <>
      <PageHeader title="Реестр партий">
        {filtered && (
          <Button
            variant="ghost"
            icon={X}
            onClick={() => {
              setParams(params.has("sort") ? { sort } : {}, { replace: true });
              setResets((n) => n + 1);
            }}
          >
            Сбросить фильтры
          </Button>
        )}
        <a className={buttonClass("secondary")} href={`/api/batches/export.xlsx?${exportQuery}`}>
          <Download className="size-4" aria-hidden="true" />
          Выгрузить в Excel
        </a>
        {canReceive && (
          <Link className={buttonClass()} to="/receipts/new">
            <Plus className="size-4" aria-hidden="true" />
            Новое поступление
          </Link>
        )}
      </PageHeader>
      <StatusBar status={params.get("status") ?? ""} onSelect={(status) => update({ status })} />
      <ErrorBox error={error} />
      {/* Таблица видна и при ошибке: иначе неверный фильтр нельзя было бы исправить. */}
      <TableCard
        footer={
          <>
            {data?.total === 0 && <Empty icon={PackageSearch}>Ничего не найдено. Ослабьте фильтры.</Empty>}
            {data && (
              <Pager
                page={page}
                pageSize={PAGE_SIZE}
                total={data.total}
                onPage={(p) => update({ page: String(p) })}
              />
            )}
          </>
        }
      >
        <thead className={theadClass}>
          <tr>
            {COLUMNS.map(([field, title]) => {
              const active = sort.replace("-", "") === field;
              const descending = active && sort.startsWith("-");
              const SortIcon = !active ? ChevronsUpDown : descending ? ArrowDown : ArrowUp;
              return (
                <th
                  key={field}
                  className={thClass}
                  aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
                >
                  <button
                    type="button"
                    className="inline-flex items-center gap-1 hover:text-ink"
                    onClick={() => update({ sort: active && !descending ? `-${field}` : field })}
                  >
                    {title}
                    <SortIcon
                      className={`size-3.5 ${active ? "text-wax" : "opacity-40"}`}
                      aria-hidden="true"
                    />
                  </button>
                </th>
              );
            })}
          </tr>
          <tr key={resets} className="align-top">
            <FilterCell name="number" params={params} update={update} />
            <FilterCell name="material" params={params} update={update} />
            <FilterCell name="supplier" params={params} update={update} />
            <th className="space-y-1 px-2 pb-2.5 font-normal">
              <DateInput label="Дата с" name="received_from" params={params} update={update} />
              <DateInput label="Дата по" name="received_to" params={params} update={update} />
            </th>
            <RangeCell name="qty_initial" params={params} update={update} />
            <RangeCell name="qty_on_hand" params={params} update={update} />
            <RangeCell name="qty_free" params={params} update={update} />
            <RangeCell name="price" params={params} update={update} />
            <SelectCell
              name="currency"
              options={[
                ["RUB", "RUB"],
                ["USD", "USD"],
                ["EUR", "EUR"],
              ]}
              params={params}
              update={update}
            />
            <RangeCell name="unit_cost_rub" params={params} update={update} />
            <SelectCell
              name="status"
              options={[
                ["on_check", "На проверке"],
                ["accepted", "Принята"],
                ["rejected", "Брак"],
              ]}
              params={params}
              update={update}
            />
          </tr>
        </thead>
        <tbody>
          {data?.items.map((b) => (
            <tr key={b.id} className={rowClass}>
              <td className={`${tdClass} whitespace-nowrap`}>
                <Link className={linkClass} to={`/batches/${b.id}`}>
                  {b.number}
                </Link>
              </td>
              <td className={`${tdClass} min-w-32`}>
                {b.material.name}
                <div className="text-xs text-muted">{b.material.code}</div>
              </td>
              <td className={tdClass}>{b.supplier.name}</td>
              <td className={`${tdClass} tabular-nums`}>{day(b.received_at)}</td>
              {[b.qty_initial, b.qty_on_hand, b.qty_free].map((qty, i) => (
                <td key={i} className={`${tdClass} whitespace-nowrap tabular-nums`}>
                  {num(qty)} {b.material.unit}
                </td>
              ))}
              <td className={`${tdClass} tabular-nums`}>{num(b.price, 2)}</td>
              <td className={tdClass}>{b.currency}</td>
              <td className={`${tdClass} whitespace-nowrap tabular-nums`}>{rub(b.unit_cost_rub)}</td>
              <td className={tdClass}>
                <Badge tone={BATCH_TONE[b.status]} dot>
                  {b.status_label}
                </Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </TableCard>
    </>
  );
}

const SEGMENTS = [
  ["accepted", "Приняты", "bg-ok"],
  ["on_check", "На проверке", "bg-amber-400"],
  ["rejected", "Брак", "bg-bad"],
] as const;

/** Сколько партий в каждом статусе. Клик включает фильтр, повторный — снимает. */
function StatusBar({ status, onSelect }: { status: string; onSelect: (status: string) => void }) {
  const counts: Record<string, number> = {
    accepted: useCount("/batches?status=accepted") ?? 0,
    on_check: useCount("/batches?status=on_check") ?? 0,
    rejected: useCount("/batches?status=rejected") ?? 0,
  };
  const total = Object.values(counts).reduce((sum, n) => sum + n, 0);
  return (
    <section
      aria-label="Партии по статусам"
      className="mb-4 flex flex-wrap items-center gap-x-8 gap-y-3 rounded-xl border border-line bg-card px-5 py-4"
    >
      <div>
        <div className="text-xs font-medium text-muted">Всего партий</div>
        <div className="mt-1 text-xl font-semibold tracking-tight tabular-nums">{total || "—"}</div>
      </div>
      <div className="min-w-64 flex-1">
        <div className="flex h-1.5 max-w-xl gap-1">
          {SEGMENTS.map(
            ([value, , color]) =>
              !!counts[value] && (
                <div key={value} className={`rounded-full ${color}`} style={{ flexGrow: counts[value] }} />
              ),
          )}
        </div>
        <div className="mt-2.5 -ml-2 flex flex-wrap gap-1">
          {SEGMENTS.map(([value, label, color]) => (
            <button
              key={value}
              type="button"
              aria-pressed={status === value}
              className={`flex items-center gap-2 rounded-md px-2 py-0.5 text-xs transition-colors hover:bg-paper ${status === value ? "bg-paper ring-1 ring-line" : ""}`}
              onClick={() => onSelect(status === value ? "" : value)}
            >
              <span className={`h-2.5 w-1 rounded-full ${color}`} />
              <span className="text-muted">{label}</span>
              <span className="font-semibold tabular-nums">{counts[value]}</span>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

const thClass = "px-2 py-2 first:pl-4 last:pr-4 text-left text-xs font-medium text-muted whitespace-nowrap";
const tdClass = "px-2 py-2.5 first:pl-4 last:pr-4 align-middle";
const filterClass = `${inputClass} h-7! rounded-md! px-1.5! text-xs!`;

interface FilterProps {
  name: string;
  params: URLSearchParams;
  update: (changes: Record<string, string>) => void;
}

/** Текстовый фильтр с паузой: запрос уходит, когда человек перестал печатать. */
function DelayedInput({
  name,
  params,
  update,
  placeholder,
  numeric = false,
}: FilterProps & { placeholder: string; numeric?: boolean }) {
  const [value, setValue] = useState(params.get(name) ?? "");
  useEffect(() => {
    if (value === (params.get(name) ?? "")) return;
    const timer = setTimeout(
      () => update({ [name]: numeric ? value.trim().replace(",", ".") : value.trim() }),
      400,
    );
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- таймер перезапускает только ввод
  }, [value]);
  return (
    <input
      className={`${filterClass} min-w-12`}
      placeholder={placeholder}
      aria-label={`Фильтр ${name}`}
      value={value}
      onChange={(e) => setValue(e.target.value)}
    />
  );
}

function FilterCell(props: FilterProps) {
  return (
    <th className="px-2 pb-2.5 font-normal first:pl-4 last:pr-4">
      <DelayedInput {...props} placeholder="содержит" />
    </th>
  );
}

function RangeCell({ name, ...rest }: FilterProps) {
  return (
    <th className="space-y-1 px-2 pb-2.5 font-normal first:pl-4 last:pr-4">
      <DelayedInput {...rest} name={`${name}_min`} placeholder="от" numeric />
      <DelayedInput {...rest} name={`${name}_max`} placeholder="до" numeric />
    </th>
  );
}

function DateInput({ label, name, params, update }: FilterProps & { label: string }) {
  return (
    <input
      type="date"
      aria-label={label}
      className={filterClass}
      value={params.get(name) ?? ""}
      onChange={(e) => update({ [name]: e.target.value })}
    />
  );
}

function SelectCell({ name, options, params, update }: FilterProps & { options: [string, string][] }) {
  return (
    <th className="px-2 pb-2.5 font-normal first:pl-4 last:pr-4">
      <select
        aria-label={`Фильтр ${name}`}
        className={`${filterClass} pr-6!`}
        value={params.get(name) ?? ""}
        onChange={(e) => update({ [name]: e.target.value })}
      >
        <option value="">все</option>
        {options.map(([value, title]) => (
          <option key={value} value={value}>
            {title}
          </option>
        ))}
      </select>
    </th>
  );
}
