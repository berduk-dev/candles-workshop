/** «1234.500» → «1 234,5». Работает со строкой: число не проходит через float. */
export function num(value: string | number | null | undefined, minDecimals = 0): string {
  if (value === null || value === undefined || value === "") return "—";
  const [whole = "0", fraction = ""] = String(value).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, " ");
  const trimmed = fraction.replace(/0+$/, "").padEnd(minDecimals, "0");
  return trimmed ? `${grouped},${trimmed}` : grouped;
}

export const isZero = (value: string) => /^-?0*\.?0*$/.test(value);

export const rub = (value: string) => `${num(value, 2)} ₽`;

/** Дата без времени: «2026-11-24» → «24.11.2026». */
export const day = (iso: string) => iso.slice(0, 10).split("-").reverse().join(".");

export const dateTime = (iso: string) =>
  new Date(iso).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

/** Сегодня в формате для <input type="date"> по местному времени. */
export function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
