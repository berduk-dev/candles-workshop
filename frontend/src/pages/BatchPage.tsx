import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { Link, useParams } from "react-router";

import {
  Badge,
  BATCH_TONE,
  Button,
  Card,
  ErrorBox,
  History,
  linkClass,
  PageHeader,
  Props,
  Spinner,
  StatStrip,
  tdClass,
  thClass,
} from "../components/ui";
import { api } from "../lib/api";
import { useCan } from "../lib/auth";
import { dateTime, day, num, rub } from "../lib/format";
import type { BatchCard } from "../lib/types";

export function BatchPage() {
  const { id } = useParams();
  const canChange = useCan("batch_status");
  const queryClient = useQueryClient();
  const { data: batch, error } = useQuery({
    queryKey: ["batch", id],
    queryFn: () => api.get<BatchCard>(`/batches/${id}`),
  });
  const change = useMutation({
    mutationFn: (status: string) => api.post(`/batches/${id}/status`, { status }),
    onSuccess: () => queryClient.invalidateQueries(),
  });

  if (error) return <ErrorBox error={error} />;
  if (!batch) return <Spinner />;
  const unit = batch.material.unit;
  return (
    <>
      <PageHeader
        parent={{ to: "/batches", label: "Партии" }}
        title={
          <>
            Партия {batch.number}
            <Badge tone={BATCH_TONE[batch.status]} dot>
              {batch.status_label}
            </Badge>
          </>
        }
      >
        {canChange && batch.status === "on_check" && (
          <>
            <Button icon={Check} onClick={() => change.mutate("accepted")}>
              Принять
            </Button>
            <Button variant="danger" icon={X} onClick={() => change.mutate("rejected")}>
              Брак
            </Button>
          </>
        )}
      </PageHeader>
      <ErrorBox error={change.error} />
      <StatStrip
        items={[
          { label: "Поступило", value: `${num(batch.qty_initial)} ${unit}` },
          { label: "Остаток", value: `${num(batch.qty_on_hand)} ${unit}` },
          { label: "В резерве", value: `${num(batch.qty_reserved)} ${unit}` },
          { label: "Свободно", value: `${num(batch.qty_free)} ${unit}` },
        ]}
      />
      <div className="grid items-start gap-4 xl:grid-cols-[1fr_1.4fr]">
        <Card title="О партии">
          <Props
            items={[
              ["Материал", `${batch.material.name} (${batch.material.code})`],
              ["Поставщик", batch.supplier.name],
              ["Поступление", `${batch.receipt_number} от ${day(batch.received_at)}`],
              ["Цена", `${num(batch.price, 2)} ${batch.currency}`],
              ["Курс", `${num(batch.fx_rate)} на ${day(batch.fx_rate_date)}`],
              ["Себестоимость единицы", rub(batch.unit_cost_rub)],
              ["Комментарий", batch.comment ?? "—"],
            ]}
          />
        </Card>
        <div className="min-w-0 space-y-4">
          <Card title="Движения">
            <div className="-mx-5 -mb-2 overflow-x-auto border-t border-line/70 px-1">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className={thClass}>Когда</th>
                    <th className={thClass}>Что</th>
                    <th className={thClass}>Количество</th>
                    <th className={thClass}>Задание</th>
                  </tr>
                </thead>
                <tbody>
                  {batch.movements.map((m) => (
                    <tr key={m.id} className="border-t border-line/70">
                      <td className={`${tdClass} whitespace-nowrap text-muted tabular-nums`}>
                        {dateTime(m.created_at)}
                      </td>
                      <td className={tdClass}>{m.kind_label}</td>
                      <td className={`${tdClass} font-medium whitespace-nowrap tabular-nums`}>
                        {m.qty.startsWith("-") ? "" : "+"}
                        {num(m.qty)} {unit}
                      </td>
                      <td className={tdClass}>
                        {m.job_id && (
                          <Link className={linkClass} to={`/jobs/${m.job_id}`}>
                            открыть
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          <Card title="История изменений">
            <History entries={batch.history} />
          </Card>
        </div>
      </div>
    </>
  );
}
