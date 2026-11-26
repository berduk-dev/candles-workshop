import { useQuery } from "@tanstack/react-query";
import { Navigate, useParams } from "react-router";

import { ErrorBox, Spinner } from "../components/ui";
import { api } from "../lib/api";

/** Переход по номеру документа из ответа ассистента: номер → id → карточка. */
export function ByNumberPage({ kind }: { kind: "jobs" | "batches" }) {
  const { number = "" } = useParams();
  const { data, error } = useQuery({
    queryKey: ["by-number", kind, number],
    queryFn: () => api.get<{ id: number }>(`/${kind}/by-number/${encodeURIComponent(number)}`),
  });
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  return <Navigate replace to={`/${kind}/${data.id}`} />;
}
