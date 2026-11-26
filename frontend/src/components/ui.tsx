import {
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  CalendarCheck,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  CircleAlert,
  FilePenLine,
  Flame,
  type LucideIcon,
  PackageCheck,
} from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router";

import { errorText } from "../lib/api";
import { dateTime, num } from "../lib/format";
import { PERIODS } from "../lib/period";
import type { AuditEntry, BatchStatus, JobStatus } from "../lib/types";

type Tone = "ok" | "warn" | "bad" | "wax" | "info" | "muted";

const TONES: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
  wax: "bg-wax-soft text-wax-dark",
  info: "bg-info-soft text-info",
  muted: "bg-paper text-muted",
};

export function Badge({
  tone = "muted",
  dot,
  children,
}: {
  tone?: Tone;
  dot?: boolean;
  children: ReactNode;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

/** Знак мастерской: свеча — просвет в плитке. Тот же контур, что в assets/logo/mark.svg. */
export function Logo({ className = "size-9" }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={`fill-ink ${className}`} aria-hidden="true">
      <path
        fillRule="evenodd"
        d="M16 0H48a16 16 0 0 1 16 16V48a16 16 0 0 1 -16 16H16a16 16 0 0 1 -16 -16V16a16 16 0 0 1 16 -16ZM32 8.8C35.19 13.97 39.6 18.53 39.6 24A7.6 7.6 0 0 1 24.4 24C24.4 18.53 28.81 13.97 32 8.8ZM21 29.04A12.1 12.1 0 0 0 43 29.04V64H21Z"
      />
    </svg>
  );
}

/** Кружок с инициалами: «Кузнецов Олег Петрович» → «КО». */
export function Avatar({ name }: { name: string }) {
  const initials = name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("");
  return (
    <span
      aria-hidden="true"
      className="grid size-8 shrink-0 place-items-center rounded-full bg-wax-soft text-xs font-semibold text-wax-dark"
    >
      {initials}
    </span>
  );
}

export const BATCH_TONE: Record<BatchStatus, Tone> = {
  on_check: "warn",
  accepted: "ok",
  rejected: "bad",
};

export const JOB_TONE: Record<JobStatus, Tone> = {
  draft: "muted",
  planned: "info",
  in_progress: "wax",
  completed: "ok",
  cancelled: "muted",
};

type Variant = "primary" | "secondary" | "danger" | "ghost";

/** Классы кнопки — отдельно, чтобы так же выглядели ссылки-кнопки. */
export function buttonClass(variant: Variant = "primary"): string {
  const styles = {
    primary: "bg-ink text-white shadow-raised hover:bg-ink/85",
    secondary: "border border-line bg-card shadow-raised hover:bg-paper",
    danger: "border border-line bg-card text-bad shadow-raised hover:border-bad/30 hover:bg-bad-soft",
    ghost: "text-muted hover:bg-paper hover:text-ink",
  }[variant];
  return `inline-flex h-9 items-center justify-center gap-2 rounded-lg px-3 text-sm font-medium whitespace-nowrap transition-colors disabled:opacity-45 disabled:pointer-events-none ${styles}`;
}

export function Button({
  variant = "primary",
  icon: Icon,
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; icon?: LucideIcon }) {
  return (
    <button type="button" className={`${buttonClass(variant)} ${className}`} {...props}>
      {Icon && <Icon className="size-4 shrink-0" aria-hidden="true" />}
      {children}
    </button>
  );
}

export const linkClass =
  "font-medium underline decoration-line decoration-2 underline-offset-4 transition-colors hover:text-wax hover:decoration-wax/40";

export const inputClass =
  "h-9 w-full rounded-lg border border-line bg-card px-2.5 text-sm transition-colors placeholder:text-muted hover:border-muted/40";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-medium text-muted">{label}</span>
      {children}
    </label>
  );
}

export function Card({
  title,
  action,
  children,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
      {title && (
        <div className="mb-4 flex min-h-7 items-center justify-between gap-3">
          <h2 className="font-semibold">{title}</h2>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  parent,
  children,
}: {
  title: ReactNode;
  parent?: { to: string; label: string };
  children?: ReactNode;
}) {
  return (
    <header className="mb-5 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3">
        {parent && (
          <Link
            to={parent.to}
            aria-label={`Назад: ${parent.label}`}
            title={parent.label}
            className={`${buttonClass("secondary")} w-9 shrink-0 px-0!`}
          >
            <ArrowLeft className="size-4 shrink-0" aria-hidden="true" />
          </Link>
        )}
        <h1 className="flex flex-wrap items-center gap-2.5 text-2xl font-semibold tracking-tight">{title}</h1>
      </div>
      <div className="flex flex-wrap gap-2">{children}</div>
    </header>
  );
}

/** Полоса показателей: ячейки в одной рамке, разделены линиями. */
export function StatStrip({ items }: { items: { label: string; value: ReactNode; hint?: ReactNode }[] }) {
  return (
    <dl className="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line lg:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="bg-card px-5 py-4">
          <dt className="text-xs font-medium text-muted">{item.label}</dt>
          <dd className="mt-1 text-xl font-semibold tracking-tight tabular-nums">{item.value}</dd>
          {item.hint && <dd className="mt-0.5 text-xs text-muted">{item.hint}</dd>}
        </div>
      ))}
    </dl>
  );
}

const STEPS: [JobStatus, string, LucideIcon][] = [
  ["draft", "Черновик", FilePenLine],
  ["planned", "Запланировано", CalendarCheck],
  ["in_progress", "В работе", Flame],
  ["completed", "Завершено", PackageCheck],
];

/** Путь задания по этапам. Пройденное — тёмное, текущее — с акцентом, отменённое — всё серое. */
export function JobSteps({ status, labels = false }: { status: JobStatus; labels?: boolean }) {
  const current = STEPS.findIndex(([step]) => step === status);
  const cancelled = status === "cancelled";
  return (
    <ol
      className="flex items-start"
      aria-label={cancelled ? "Задание отменено" : `Этап: ${STEPS[current]?.[1]}`}
    >
      {STEPS.map(([step, label, StepIcon], i) => {
        const done = i < current || (i === current && status === "completed");
        const active = i === current && !done;
        const circle = done ? "bg-ink text-white" : active ? "bg-flame text-ink" : "bg-paper text-muted/70";
        return (
          <li
            key={step}
            aria-current={active ? "step" : undefined}
            className={`flex items-start ${labels && i > 0 ? "flex-1" : ""}`}
          >
            {i > 0 && (
              <span
                className={`h-0.5 ${labels ? "mx-2 mt-[17px] min-w-4 flex-1" : "mt-[11px] w-5"} ${i <= current ? "bg-ink" : "bg-line"}`}
              />
            )}
            <span className="flex flex-col items-center gap-1.5" title={labels ? undefined : label}>
              <span
                className={`grid place-items-center rounded-full ${labels ? "size-9" : "size-6"} ${circle}`}
              >
                <StepIcon className={labels ? "size-4" : "size-3.5"} aria-hidden="true" />
              </span>
              <span
                className={
                  labels
                    ? `text-xs whitespace-nowrap max-sm:sr-only ${active || done ? "font-medium" : "text-muted"}`
                    : "sr-only"
                }
              >
                {label}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Выбор одного из нескольких коротких значений. */
export function Segmented({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly string[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div role="group" aria-label={label} className="flex rounded-lg bg-paper p-0.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          aria-pressed={option === value}
          className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${option === value ? "bg-card shadow-raised" : "text-muted hover:text-ink"}`}
          onClick={() => onChange(option)}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

/** Выбор периода списка. «Свои даты» появляются, когда даты введены вручную в колонке. */
export function PeriodPicker({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (period: string) => void;
}) {
  const options: readonly (readonly [string, string])[] =
    value === "custom" ? [...PERIODS, ["custom", "Свои даты"]] : PERIODS;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted">{label}</span>
      <div role="group" aria-label={label} className="flex flex-wrap rounded-lg bg-paper p-0.5">
        {options.map(([key, title]) => (
          <button
            key={key}
            type="button"
            aria-pressed={key === value}
            className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${key === value ? "bg-card shadow-raised" : "text-muted hover:text-ink"}`}
            onClick={() => onChange(key)}
          >
            {title}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Заголовок колонки, который сортирует список: первый клик — по возрастанию, второй — по убыванию. */
export function SortableTh({
  field,
  title,
  sort,
  onSort,
  className = thClass,
}: {
  field: string;
  title: string;
  sort: string;
  onSort: (sort: string) => void;
  className?: string;
}) {
  const active = sort.replace("-", "") === field;
  const descending = active && sort.startsWith("-");
  const Icon = !active ? ChevronsUpDown : descending ? ArrowDown : ArrowUp;
  return (
    <th className={className} aria-sort={active ? (descending ? "descending" : "ascending") : "none"}>
      <button
        type="button"
        className="inline-flex items-center gap-1 hover:text-ink"
        onClick={() => onSort(active && !descending ? `-${field}` : field)}
      >
        {title}
        <Icon className={`size-3.5 ${active ? "text-wax" : "opacity-40"}`} aria-hidden="true" />
      </button>
    </th>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <div role="alert" className="my-3 flex items-start gap-2 rounded-lg bg-bad-soft px-3 py-2.5 text-bad">
      <CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      {errorText(error)}
    </div>
  );
}

export function Spinner() {
  return (
    <div role="status" className="animate-pulse space-y-3 p-6">
      <span className="sr-only">Загрузка…</span>
      <div className="h-7 w-56 rounded-lg bg-line/70" />
      <div className="h-24 rounded-xl bg-line/50" />
      <div className="h-64 rounded-xl bg-line/50" />
    </div>
  );
}

export function Empty({ icon: Icon, children }: { icon: LucideIcon; children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 px-4 py-12 text-center text-muted">
      <span className="grid size-11 place-items-center rounded-xl border border-line bg-card shadow-raised">
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <p className="max-w-sm">{children}</p>
    </div>
  );
}

export function Props({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="divide-y divide-line/70">
      {items.map(([name, value]) => (
        <div key={name} className="flex justify-between gap-6 py-2 first:pt-0 last:pb-0">
          <dt className="shrink-0 text-muted">{name}</dt>
          <dd className="text-right tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

const ACTIONS: Record<string, string> = {
  created: "Создание",
  updated: "Изменение",
  status_changed: "Смена статуса",
  planned: "Планирование",
  reserved_more: "Дозарезервировано",
  started: "Запуск",
  completed: "Завершение",
  cancelled: "Отмена",
  marking_retry: "Повтор отправки в маркировку",
};

export const actionLabel = (action: string) => ACTIONS[action] ?? action;

const FIELDS: Record<string, string> = {
  status: "статус",
  number: "номер",
  qty: "количество",
  planned_date: "плановая дата",
  produced_qty: "выпущено",
  deficit: "дефицит",
  actual: "фактический расход",
  name: "название",
  items: "состав",
  recipe_id: "рецепт №",
  received_at: "дата поступления",
  supplier_id: "поставщик №",
  material_id: "материал №",
  lines: "строк",
  qty_initial: "поступило",
  qty_on_hand: "остаток",
  qty_reserved: "в резерве",
  price: "цена",
  currency: "валюта",
  fx_rate: "курс",
  fx_rate_date: "дата курса",
  unit_cost_rub: "себестоимость, ₽",
  comment: "комментарий",
  code: "код",
  unit: "единица",
};

const VALUES: Record<string, string> = {
  on_check: "На проверке",
  accepted: "Принята",
  rejected: "Брак",
  draft: "Черновик",
  planned: "Запланировано",
  in_progress: "В работе",
  completed: "Завершено",
  cancelled: "Отменено",
};

function show(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") {
    const pairs = Object.entries(value).map(([k, v]) => `${k} ${num(String(v))}`);
    return pairs.join(", ") || "нет";
  }
  return VALUES[String(value)] ?? String(value);
}

/** «что было → что стало» */
export function Changes({ entry }: { entry: AuditEntry }) {
  const keys = Object.keys(entry.after ?? {});
  return (
    <ul className="flex flex-wrap gap-x-4 text-xs text-muted">
      {keys.map((key) => (
        <li key={key}>
          {FIELDS[key] ?? key}: {entry.before?.[key] !== undefined && `${show(entry.before[key])} → `}
          {show(entry.after?.[key])}
        </li>
      ))}
    </ul>
  );
}

export function History({ entries }: { entries: AuditEntry[] }) {
  return (
    <ol>
      {entries.map((e, i) => (
        <li key={e.id} className="relative pb-4 pl-5 last:pb-0">
          {i < entries.length - 1 && <span className="absolute top-3 bottom-0 left-[3px] w-px bg-line" />}
          <span className="absolute top-1.5 left-0 size-[7px] rounded-full bg-muted/60" />
          <div className="font-medium">{actionLabel(e.action)}</div>
          <div className="text-xs text-muted">
            {e.user_full_name} · {dateTime(e.at)}
          </div>
          {e.action !== "created" && <Changes entry={e} />}
        </li>
      ))}
    </ol>
  );
}

/** Рамка таблицы: шапка на сером, прокрутка вбок, снизу — страницы. */
export function TableCard({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="overflow-hidden rounded-xl border border-line bg-card">
      <div className="relative overflow-x-auto">
        <table className="w-full">{children}</table>
      </div>
      {footer}
    </div>
  );
}

export const theadClass = "border-b border-line bg-paper/70";
export const rowClass = "border-b border-line/70 transition-colors last:border-0 hover:bg-paper/60";
export const thClass = "px-4 py-2.5 text-left text-xs font-medium text-muted whitespace-nowrap";
export const tdClass = "px-4 py-3 align-middle";

/** Номера страниц: первая, последняя и соседи текущей; null — пропуск. */
function pageNumbers(page: number, last: number): (number | null)[] {
  const near = [1, page - 1, page, page + 1, last].filter((n) => n >= 1 && n <= last);
  const unique = [...new Set(near)];
  return unique.flatMap((n, i) => (i > 0 && n - unique[i - 1]! > 1 ? [null, n] : [n]));
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  if (total === 0) return null;
  const last = Math.max(1, Math.ceil(total / pageSize));
  const square =
    "grid h-8 min-w-8 place-items-center rounded-lg border px-2 text-sm tabular-nums transition-colors disabled:opacity-40";
  return (
    <nav
      aria-label="Страницы"
      className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3"
    >
      <span className="text-muted tabular-nums">
        Показано {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} из {total}
      </span>
      {last > 1 && (
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            aria-label="Предыдущая страница"
            className={`${square} border-line enabled:hover:bg-paper`}
            disabled={page <= 1}
            onClick={() => onPage(page - 1)}
          >
            <ChevronLeft className="size-4" aria-hidden="true" />
          </button>
          {pageNumbers(page, last).map((n, i) =>
            n === null ? (
              <span key={`gap${i}`} className="px-1 text-muted">
                …
              </span>
            ) : (
              <button
                key={n}
                type="button"
                aria-label={`Страница ${n}`}
                aria-current={n === page ? "page" : undefined}
                className={`${square} ${n === page ? "border-wax bg-wax font-medium text-white" : "border-line hover:bg-paper"}`}
                onClick={() => onPage(n)}
              >
                {n}
              </button>
            ),
          )}
          <button
            type="button"
            aria-label="Следующая страница"
            className={`${square} border-line enabled:hover:bg-paper`}
            disabled={page >= last}
            onClick={() => onPage(page + 1)}
          >
            <ChevronRight className="size-4" aria-hidden="true" />
          </button>
        </div>
      )}
    </nav>
  );
}
