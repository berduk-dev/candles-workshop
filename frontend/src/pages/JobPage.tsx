import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, PackagePlus, Pencil, Play, RotateCw, TriangleAlert } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router";

import {
  Badge,
  Button,
  Card,
  ErrorBox,
  Field,
  History,
  inputClass,
  JOB_TONE,
  JobSteps,
  linkClass,
  PageHeader,
  Segmented,
  Spinner,
  tdClass,
  thClass,
} from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { dateTime, day, isZero, num } from "../lib/format";
import type { Component, JobCard, JobCost } from "../lib/types";
import { JobForm } from "./JobsPage";

const LIGHTS = {
  green: ["bg-ok-soft text-ok", "Всё в резерве"],
  yellow: ["bg-warn-soft text-warn", "Часть"],
  red: ["bg-bad-soft text-bad", "Ничего"],
} as const;

export function JobPage() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const canManage = useCan("job_manage");
  const canCancel = useCan("job_cancel");
  const canRetry = useCan("marking_retry");
  const [editing, setEditing] = useState(false);
  const [completing, setCompleting] = useState(false);
  const { data: job, error } = useQuery({
    queryKey: ["job", id],
    queryFn: () => api.get<JobCard>(`/jobs/${id}`),
    // Пока отчёт уходит в маркировку, обновляем карточку сами.
    refetchInterval: (q) => (q.state.data?.marking?.status === "pending" ? 4000 : false),
  });
  const act = useMutation({
    mutationFn: ({ step, body }: { step: string; body?: unknown }) =>
      api.post<JobCard>(`/jobs/${id}/${step}`, body),
    onSuccess: async () => {
      setCompleting(false);
      await queryClient.invalidateQueries();
    },
  });

  if (error) return <ErrorBox error={error} />;
  if (!job) return <Spinner />;
  const run = (step: string, body?: unknown) => act.mutate({ step, body });

  return (
    <>
      <PageHeader
        parent={{ to: "/jobs", label: "Задания" }}
        title={
          <>
            Задание {job.number}
            <Badge tone={JOB_TONE[job.status]}>{job.status_label}</Badge>
            {job.has_deficit && (
              <Badge tone="bad">
                <TriangleAlert className="size-3.5" aria-hidden="true" />
                дефицит
              </Badge>
            )}
          </>
        }
      >
        {canManage && job.status === "draft" && (
          <>
            <Button variant="secondary" icon={Pencil} onClick={() => setEditing((v) => !v)}>
              Править
            </Button>
            <Button icon={Check} onClick={() => run("plan")}>
              Запланировать
            </Button>
          </>
        )}
        {canManage && job.status === "planned" && (
          <>
            {job.has_deficit && (
              // Кнопка добирает резерв из свободного остатка. Если по дефицитным материалам
              // свободного нет (ждём поступления), нажимать бессмысленно — объясняем почему.
              <Button
                variant="secondary"
                icon={PackagePlus}
                disabled={!canTopUp(job)}
                title={
                  canTopUp(job)
                    ? "Добрать недостающее из свободного остатка"
                    : "Свободного остатка по дефицитным материалам нет — сначала нужно поступление"
                }
                onClick={() => run("reserve")}
              >
                Дозарезервировать
              </Button>
            )}
            <Button icon={Play} onClick={() => run("start")}>
              Запустить
            </Button>
          </>
        )}
        {canManage && job.status === "in_progress" && (
          <Button icon={Check} onClick={() => setCompleting((v) => !v)}>
            Завершить…
          </Button>
        )}
        {canCancel && (job.status === "draft" || job.status === "planned") && (
          <Button variant="danger" icon={Ban} onClick={() => run("cancel")}>
            Отменить
          </Button>
        )}
      </PageHeader>
      <section className="mb-4 flex flex-wrap items-center justify-between gap-x-10 gap-y-5 rounded-xl border border-line bg-card p-5">
        <div>
          <div className="text-base font-semibold">{job.recipe_name}</div>
          <div className="mt-0.5 text-muted tabular-nums">
            {num(job.qty)} шт на {day(job.planned_date)}
            {job.produced_qty !== null && `, выпущено ${num(job.produced_qty)} шт`}
          </div>
        </div>
        <div className="w-full max-w-xl">
          <JobSteps status={job.status} labels />
        </div>
      </section>
      <ErrorBox error={act.error} />
      {/* Править можно только черновик: после «Запланировать» форма исчезает сама. */}
      {editing && job.status === "draft" && <JobForm job={job} onSaved={() => setEditing(false)} />}
      {completing && (
        <CompleteForm job={job} pending={act.isPending} onSubmit={(body) => run("complete", body)} />
      )}
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <div className="min-w-0 space-y-4">
          <Card title="Компоненты">
            <Components job={job} />
          </Card>
          {job.reservations.length > 0 && (
            <Card title="Резерв по партиям (FIFO)">
              <ul className="divide-y divide-line/70">
                {job.reservations.map((r) => (
                  <li
                    key={r.batch_id}
                    className="flex flex-wrap items-center justify-between gap-x-4 py-2 first:pt-0 last:pb-0"
                  >
                    <span>
                      <Link className={linkClass} to={`/batches/${r.batch_id}`}>
                        {r.batch_number}
                      </Link>{" "}
                      <span className="text-muted">
                        от {day(r.received_at)} · {r.material_code}
                      </span>
                    </span>
                    <span className="font-medium tabular-nums">
                      {num(r.qty)} {r.unit}
                    </span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {job.marking && (
            <Card title="Сервис маркировки">
              <div className="mb-3 flex items-center gap-3">
                <Badge
                  dot
                  tone={{ pending: "warn", sent: "ok", failed: "bad" }[job.marking.status] as "warn"}
                >
                  {job.marking.status_label}
                </Badge>
                {job.marking.status === "pending" && (
                  <span className="text-xs text-muted">
                    следующая попытка {dateTime(job.marking.next_attempt_at)}
                  </span>
                )}
                {canRetry && job.marking.status === "failed" && (
                  <Button variant="secondary" icon={RotateCw} onClick={() => run("marking/retry")}>
                    Повторить
                  </Button>
                )}
              </div>
              <ol className="space-y-1 text-sm">
                {job.marking.log.map((a, i) => (
                  <li key={i} className={a.error ? "text-bad" : "text-ok"}>
                    {dateTime(a.at)} · попытка {a.number} · {a.error ?? "отправлен"} ({num(a.duration_ms)} мс)
                  </li>
                ))}
              </ol>
            </Card>
          )}
        </div>
        <div className="min-w-0 space-y-4">
          <Cost job={job} />
          <Card title="История изменений">
            <History entries={job.history} />
          </Card>
        </div>
      </div>
    </>
  );
}

function Components({ job }: { job: JobCard }) {
  const planned = job.status === "planned";
  const started = job.status === "in_progress" || job.status === "completed";
  return (
    <div className="-mx-5 -mb-2 overflow-x-auto border-t border-line/70 px-1">
      <table className="w-full">
        <thead>
          <tr>
            <th className={thClass}>Материал</th>
            {planned && <th className={thClass}>Резерв</th>}
            <th className={thClass}>Нужно</th>
            {planned && <th className={thClass}>В резерве</th>}
            {planned && <th className={thClass}>Дефицит</th>}
            {started && <th className={thClass}>Выдано</th>}
            {job.status === "completed" && <th className={thClass}>Факт</th>}
            {!started && <th className={thClass}>Свободно</th>}
          </tr>
        </thead>
        <tbody>
          {job.components.map((c) => (
            <tr key={c.material.id} className="border-t border-line/70">
              <td className={tdClass}>
                {c.material.name}
                <div className="text-xs text-muted">{c.material.code}</div>
              </td>
              {planned && (
                <td className={tdClass}>
                  <Light c={c} />
                </td>
              )}
              <Qty value={c.qty_required} unit={c.material.unit} />
              {planned && <Qty value={c.qty_reserved} unit={c.material.unit} />}
              {planned && (
                <td
                  className={`${tdClass} tabular-nums whitespace-nowrap ${isZero(c.qty_deficit) ? "text-muted" : "font-semibold text-bad"}`}
                >
                  {isZero(c.qty_deficit) ? "—" : `${num(c.qty_deficit)} ${c.material.unit}`}
                </td>
              )}
              {started && <Qty value={c.qty_issued} unit={c.material.unit} />}
              {job.status === "completed" && <Qty value={c.qty_actual} unit={c.material.unit} />}
              {!started && <Qty value={c.qty_free} unit={c.material.unit} />}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Light({ c }: { c: Component }) {
  if (!c.light) return null;
  const [color, title] = LIGHTS[c.light];
  return (
    <span
      data-light={c.light}
      className={`inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium whitespace-nowrap ${color}`}
    >
      <span className="size-1.5 rounded-full bg-current" />
      {title}
    </span>
  );
}

function Qty({ value, unit }: { value: string | null; unit: string }) {
  return (
    <td className={`${tdClass} whitespace-nowrap tabular-nums`}>
      {value === null ? "—" : `${num(value)} ${unit}`}
    </td>
  );
}

function Cost({ job }: { job: JobCard }) {
  const allowed = useCan("job_cost_view");
  const [currency, setCurrency] = useState("RUB");
  const { data, error } = useQuery({
    queryKey: ["job-cost", job.id, currency, job.status],
    queryFn: () => api.get<JobCost>(`/jobs/${job.id}/cost?currency=${currency}`),
    enabled: allowed && job.status !== "cancelled",
  });
  if (!allowed || job.status === "cancelled") return null;
  return (
    <Card
      title="Себестоимость"
      action={
        <Segmented label="Валюта" options={["RUB", "USD", "EUR"]} value={currency} onChange={setCurrency} />
      }
    >
      <ErrorBox error={error} />
      {data && (
        <div className="space-y-1">
          <div className="text-2xl font-semibold tracking-tight tabular-nums">
            {num(data.total, 2)} {data.currency}
          </div>
          {data.per_unit && (
            <div className="tabular-nums">
              {num(data.per_unit, 2)} {data.currency} за штуку
            </div>
          )}
          <div className="text-xs text-muted">
            Основание: {data.basis}
            {!data.is_complete && " — оценена не полностью из-за дефицита"}
          </div>
        </div>
      )}
    </Card>
  );
}

function CompleteForm({
  job,
  pending,
  onSubmit,
}: {
  job: JobCard;
  pending: boolean;
  onSubmit: (body: unknown) => void;
}) {
  const [produced, setProduced] = useState(String(job.qty));
  const [actual, setActual] = useState<Record<string, string>>(
    Object.fromEntries(job.components.map((c) => [c.material.code, num(c.qty_required).replaceAll(" ", "")])),
  );

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit({
      produced_qty: Number(produced),
      actual: Object.entries(actual).map(([material_code, qty]) => ({
        material_code,
        qty: qty.trim().replace(",", "."),
      })),
    });
  }

  return (
    <div className="mb-4">
      <Card title="Завершение: фактический расход">
        <form onSubmit={submit}>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <Field label="Выпущено, шт">
              <input
                className={inputClass}
                type="number"
                min={0}
                value={produced}
                onChange={(e) => setProduced(e.target.value)}
                required
              />
            </Field>
            {job.components.map((c) => (
              <Field
                key={c.material.id}
                label={`${c.material.name}, ${c.material.unit} (выдано ${num(c.qty_issued)})`}
              >
                <input
                  className={inputClass}
                  inputMode="decimal"
                  value={actual[c.material.code]}
                  onChange={(e) => setActual((a) => ({ ...a, [c.material.code]: e.target.value }))}
                  required
                />
              </Field>
            ))}
          </div>
          <p className="my-3 text-xs text-muted">
            Перерасход досписывается со склада, экономия возвращается в партии.
          </p>
          <Button type="submit" disabled={pending}>
            Завершить задание
          </Button>
        </form>
      </Card>
    </div>
  );
}

/** Есть что добрать: у дефицитного материала появился свободный остаток. */
function canTopUp(job: JobCard): boolean {
  return job.components.some((c) => !isZero(c.qty_deficit) && !isZero(c.qty_free));
}
