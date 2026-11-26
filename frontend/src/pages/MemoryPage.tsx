import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, Trash2 } from "lucide-react";

import { Button, Card, Empty, ErrorBox, PageHeader, Spinner } from "../components/ui";
import { api } from "../lib/api";
import { dateTime } from "../lib/format";
import type { Fact } from "../lib/types";

export function MemoryPage() {
  const queryClient = useQueryClient();
  const { data, error } = useQuery({
    queryKey: ["memory"],
    queryFn: () => api.get<Fact[]>("/chat/memory"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/chat/memory/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["memory"] }),
  });
  return (
    <div className="max-w-2xl">
      <PageHeader title="Что ассистент помнит обо мне" />
      <ErrorBox error={error ?? remove.error} />
      {!data && !error && <Spinner />}
      {data && (
        <Card>
          {data.length === 0 && (
            <Empty icon={Brain}>
              Пока ничего. Напишите ассистенту, например: «запомни, я считаю в евро».
            </Empty>
          )}
          <ul className="divide-y divide-line/70">
            {data.map((f) => (
              <li key={f.id} className="flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0">
                <span>
                  {f.fact}
                  <span className="mt-0.5 block text-xs text-muted">{dateTime(f.created_at)}</span>
                </span>
                <Button variant="danger" icon={Trash2} onClick={() => remove.mutate(f.id)}>
                  Удалить
                </Button>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <p className="mt-3 text-xs text-muted">
        Факты видны только вам и используются только в ваших диалогах.
      </p>
    </div>
  );
}
