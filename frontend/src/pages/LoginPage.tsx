import { ClipboardList, Package, PackageCheck, Truck } from "lucide-react";
import { type FormEvent, useState } from "react";

import wordmark from "../assets/logo/wordmark.svg";
import { Button, ErrorBox, Field, inputClass, Logo } from "../components/ui";
import { api } from "../lib/api";

const DEMO = [
  ["storekeeper", "Кладовщик"],
  ["technologist", "Технолог"],
  ["manager", "Руководитель"],
] as const;

const PATH = [
  [Truck, "Поступление", "Сырьё от поставщика, цена в рублях, долларах или евро"],
  [Package, "Партия", "Проверка качества и остаток по каждой партии"],
  [ClipboardList, "Задание", "Резерв по FIFO, выдача и фактический расход"],
  [PackageCheck, "Маркировка", "Отчёт о выпуске уходит в сервис маркировки"],
] as const;

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.post("/auth/login", { login, password });
      onLogin();
    } catch (e) {
      setError(e);
    }
  }

  return (
    <div className="flex min-h-dvh bg-card">
      <aside className="relative isolate m-2 hidden w-[26rem] shrink-0 flex-col overflow-hidden rounded-2xl bg-paper p-9 lg:flex">
        <div className="flex items-center gap-3">
          <Logo />
          <img src={wordmark} alt="Свечная мастерская" className="h-7" />
        </div>
        <ol className="mt-16">
          {PATH.map(([Icon, title, text], i) => (
            <li key={title} className="relative flex gap-4 pb-8">
              {i < PATH.length - 1 && <span className="absolute top-10 bottom-0 left-[19px] w-px bg-line" />}
              <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-line bg-card shadow-raised">
                <Icon className="size-4.5" aria-hidden="true" />
              </span>
              <div>
                <div className="font-medium">{title}</div>
                <div className="text-muted">{text}</div>
              </div>
            </li>
          ))}
        </ol>
        {/* Пламя из знака, крупно, за краем панели и под текстом */}
        <svg
          viewBox="24.4 8.8 15.2 22.8"
          className="absolute -right-16 -bottom-36 -z-10 w-72 fill-line/60"
          aria-hidden="true"
        >
          <path d="M32 8.8C35.19 13.97 39.6 18.53 39.6 24A7.6 7.6 0 0 1 24.4 24C24.4 18.53 28.81 13.97 32 8.8Z" />
        </svg>
      </aside>
      <main className="grid flex-1 place-items-center p-6">
        <form onSubmit={(e) => void submit(e)} className="w-full max-w-sm space-y-4">
          <div className="mb-8">
            <Logo className="mb-5 size-11 lg:hidden" />
            <h1 className="text-2xl font-semibold tracking-tight">Вход в систему</h1>
            <p className="mt-1 text-muted">Учёт производства и ассистент</p>
          </div>
          <Field label="Логин">
            <input
              className={inputClass}
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              autoComplete="username"
              autoFocus
            />
          </Field>
          <Field label="Пароль">
            <input
              className={inputClass}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </Field>
          <ErrorBox error={error} />
          <Button type="submit" className="w-full">
            Войти
          </Button>
          <div className="border-t border-line pt-5">
            <p className="mb-2 text-xs text-muted">Демо-логины, пароль в README</p>
            <div className="grid grid-cols-3 gap-2">
              {DEMO.map(([value, label]) => (
                <Button
                  key={value}
                  variant="secondary"
                  className={`px-1! text-xs! ${login === value ? "border-wax/50 bg-wax-soft" : ""}`}
                  aria-pressed={login === value}
                  onClick={() => setLogin(value)}
                >
                  {label}
                </Button>
              ))}
            </div>
          </div>
        </form>
      </main>
    </div>
  );
}
