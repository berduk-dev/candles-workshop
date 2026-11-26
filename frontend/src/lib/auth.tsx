import { useQuery } from "@tanstack/react-query";
import { createContext, type ReactNode, useContext } from "react";

import { Spinner } from "../components/ui";
import { LoginPage } from "../pages/LoginPage";
import { api, ApiError } from "./api";
import type { Me } from "./types";

const MeContext = createContext<Me | null>(null);

/** Показывает приложение только вошедшему пользователю. Права здесь — лишь чтобы не рисовать
 *  лишние кнопки: настоящая проверка на бэкенде. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["me"],
    queryFn: () => api.get<Me>("/auth/me"),
    retry: (count, e) => !(e instanceof ApiError && e.status === 401) && count < 2,
    staleTime: Infinity,
  });
  if (isPending) return <Spinner />;
  if (error || !data) return <LoginPage onLogin={() => void refetch()} />;
  return <MeContext value={data}>{children}</MeContext>;
}

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe вне AuthGate");
  return me;
}

export function useCan(permission: string): boolean {
  return useMe().permissions.includes(permission);
}
