import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ClipboardList, Plus, TriangleAlert, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorBox,
  Field,
  inputClass,
  JOB_TONE,
  JobSteps,
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
import { useCan } from "../lib/auth";
import { useCount } from "../lib/counts";
import { day, num, todayIso } from "../lib/format";
import type { Job, JobCard, Page, Recipe } from "../lib/types";

const STATUSES = [
  ["", "Все"],
  ["draft", "Черновики"],
  ["planned", "Запланировано"],
  ["in_progress", "В работе"],
  ["completed", "Завершено"],
  ["cancelled", "Отменено"],
  ["overdue", "Просроченные"],
] as const;

/** Вкладка → параметры запроса. «Просроченные» — не статус, а отдельный фильтр бэкенда. */
const filterQuery = (tab: string) => (tab === "overdue" ? "overdue=true" : tab ? `status=${tab}` : "");

export function JobsPage() {
  const [params, setParams] = useSearchParams();
  const [creating, setCreating] = useState(false);
  const canManage = useCan("job_manage");
  const status = params.get("status") ?? "";
  const page = Number(params.get("page") ?? 1);
  const query = new URLSearchParams({ page: String(page), page_size: "20" });
  for (const [key, value] of new URLSearchParams(filterQuery(status))) query.set(key, value);

  const { data, error } = useQuery({
    queryKey: ["jobs", query.toString()],
    queryFn: () => api.get<Page<Job>>(`/jobs?${query}`),
    placeholderData: keepPreviousData,
  });

  return (
    <>
      <PageHeader title="Производственные задания">
        {canManage && (
          <Button
            variant={creating ? "secondary" : "primary"}
            icon={creating ? X : Plus}
            onClick={() => setCreating((v) => !v)}
          >
            {creating ? "Закрыть форму" : "Новое задание"}
          </Button>
        )}
      </PageHeader>
      {creating && <JobForm />}
      <div className="mb-4 flex gap-6 overflow-x-auto border-b border-line">
        {STATUSES.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={status === value}
            className={`flex items-center gap-2 border-b-2 pb-2.5 whitespace-nowrap transition-colors ${status === value ? "border-flame font-medium" : "border-transparent text-muted hover:text-ink"}`}
            onClick={() => setParams(value ? { status: value } : {})}
          >
            {label}
            <StatusCount status={value} />
          </button>
        ))}
      </div>
      <ErrorBox error={error} />
      {!data && !error && <Spinner />}
      {data && (
        <TableCard
          footer={
            data.total === 0 ? (
              <Empty icon={ClipboardList}>Заданий с таким статусом нет.</Empty>
            ) : (
              <Pager
                page={page}
                pageSize={20}
                total={data.total}
                onPage={(p) => setParams({ ...(status && { status }), page: String(p) })}
              />
            )
          }
        >
          <thead className={theadClass}>
            <tr>
              {["Номер", "Рецепт", "Количество", "Этапы", "Статус", "Плановая дата"].map((t) => (
                <th key={t} className={thClass}>
                  {t}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.items.map((job) => (
              <tr key={job.id} className={rowClass}>
                <td className={`${tdClass} whitespace-nowrap`}>
                  <Link className={linkClass} to={`/jobs/${job.id}`}>
                    {job.number}
                  </Link>
                </td>
                <td className={tdClass}>{job.recipe_name}</td>
                <td className={`${tdClass} whitespace-nowrap tabular-nums`}>{num(job.qty)} шт</td>
                <td className={tdClass}>
                  <JobSteps status={job.status} />
                </td>
                <td className={tdClass}>
                  <Badge tone={JOB_TONE[job.status]}>{job.status_label}</Badge>
                </td>
                <td className={`${tdClass} whitespace-nowrap`}>
                  <span className="mr-2 tabular-nums">{day(job.planned_date)}</span>
                  {job.has_deficit && (
                    <Badge tone="bad">
                      <TriangleAlert className="size-3.5" aria-hidden="true" />
                      дефицит
                    </Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </>
  );
}

function StatusCount({ status }: { status: string }) {
  const count = useCount(`/jobs?${filterQuery(status)}`);
  if (count === undefined) return null;
  return (
    <span className="rounded-md bg-paper px-1.5 text-xs font-medium text-muted tabular-nums">{count}</span>
  );
}

/** Создание черновика или правка существующего. */
export function JobForm({ job, onSaved }: { job?: JobCard; onSaved?: () => void }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: recipes } = useQuery({
    queryKey: ["recipes"],
    queryFn: () => api.get<Recipe[]>("/recipes"),
  });
  const [recipeId, setRecipeId] = useState(job ? String(job.recipe_id) : "");
  const [qty, setQty] = useState(job ? String(job.qty) : "");
  const [date, setDate] = useState(job?.planned_date ?? todayIso());
  const save = useMutation({
    mutationFn: () => {
      const body = { recipe_id: Number(recipeId), qty: Number(qty), planned_date: date };
      return job ? api.put<JobCard>(`/jobs/${job.id}`, body) : api.post<JobCard>("/jobs", body);
    },
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries();
      if (onSaved) onSaved();
      else await navigate(`/jobs/${saved.id}`);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <div className="mb-4">
      <Card title={job ? "Правка черновика" : "Черновик задания"}>
        <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
          <Field label="Рецепт">
            <select
              className={inputClass}
              value={recipeId}
              onChange={(e) => setRecipeId(e.target.value)}
              required
            >
              <option value="">Выберите…</option>
              {recipes?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Количество, шт">
            <input
              className={inputClass}
              type="number"
              min={1}
              value={qty}
              onChange={(e) => setQty(e.target.value)}
              required
            />
          </Field>
          <Field label="Плановая дата">
            <input
              className={inputClass}
              type="date"
              min={todayIso()}
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
          </Field>
          <Button type="submit" disabled={save.isPending}>
            Сохранить черновик
          </Button>
        </form>
        <ErrorBox error={save.error} />
      </Card>
    </div>
  );
}
