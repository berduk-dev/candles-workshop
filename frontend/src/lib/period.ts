/** Период для списков: готовые варианты и свои даты. По умолчанию — этот месяц. */

export const PERIODS = [
  ["month", "Этот месяц"],
  ["prev_month", "Прошлый месяц"],
  ["quarter", "3 месяца"],
  ["year", "Этот год"],
  ["all", "Всё время"],
] as const;

export const DEFAULT_PERIOD = "month";

export interface Range {
  from?: string;
  to?: string;
}

/** Дата в формате ГГГГ-ММ-ДД по местному времени (toISOString сдвинул бы её в UTC). */
const iso = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Первый и последний день месяцев: с `fromMonth` назад до `toMonth` назад (0 — текущий). */
function months(fromMonth: number, toMonth: number): Range {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() - fromMonth, 1);
  const last = new Date(now.getFullYear(), now.getMonth() - toMonth + 1, 0);
  return { from: iso(first), to: iso(last) };
}

export function periodRange(period: string): Range {
  switch (period) {
    case "month":
      return months(0, 0); // весь месяц, а не «до сегодня»: у заданий бывают даты впереди
    case "prev_month":
      return months(1, 1);
    case "quarter":
      return months(2, 0);
    case "year": {
      const year = new Date().getFullYear();
      return { from: `${year}-01-01`, to: `${year}-12-31` };
    }
    default:
      return {};
  }
}

/** Период из адреса страницы: готовый вариант или свои даты (period=custom). */
export function readPeriod(params: URLSearchParams, fromKey: string, toKey: string) {
  const period = params.get("period") ?? DEFAULT_PERIOD;
  const range: Range =
    period === "custom"
      ? { from: params.get(fromKey) ?? undefined, to: params.get(toKey) ?? undefined }
      : periodRange(period);
  return { period, range };
}

/** Добавляет границы периода в запрос к API. */
export function withRange(query: URLSearchParams, range: Range, fromKey: string, toKey: string) {
  query.delete("period");
  query.delete(fromKey);
  query.delete(toKey);
  if (range.from) query.set(fromKey, range.from);
  if (range.to) query.set(toKey, range.to);
  return query;
}
