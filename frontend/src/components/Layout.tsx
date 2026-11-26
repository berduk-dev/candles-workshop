import { useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  Brain,
  ChevronsLeft,
  ClipboardList,
  LogOut,
  type LucideIcon,
  Menu,
  MessageSquareText,
  Package,
  ScrollText,
} from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";

import wordmark from "../assets/logo/wordmark-short.svg";
import { api } from "../lib/api";
import { useCan, useMe } from "../lib/auth";
import { useCount } from "../lib/counts";
import { Avatar, Logo } from "./ui";

interface Item {
  to: string;
  label: string;
  icon: LucideIcon;
  count?: number;
}

export function Layout() {
  const me = useMe();
  const canAudit = useCan("audit_view");
  const queryClient = useQueryClient();
  const activeJobs = useCount("/jobs?status=planned&status=in_progress");
  const onCheck = useCount("/batches?status=on_check");
  const [open, setOpen] = useState(false); // выезжающее меню на узком экране
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("sidebar") === "collapsed");

  function toggleCollapsed() {
    localStorage.setItem("sidebar", collapsed ? "open" : "collapsed");
    setCollapsed(!collapsed);
  }

  async function logout() {
    await api.post("/auth/logout").catch(() => undefined);
    queryClient.clear(); // чужие данные не должны остаться в кэше для следующего пользователя
    window.location.href = "/";
  }

  const groups: [string, Item[]][] = [
    [
      "Помощник",
      [
        { to: "/chat", label: "Ассистент", icon: MessageSquareText },
        { to: "/memory", label: "Память", icon: Brain },
      ],
    ],
    [
      "Производство",
      [
        { to: "/jobs", label: "Задания", icon: ClipboardList, count: activeJobs },
        { to: "/batches", label: "Партии", icon: Package, count: onCheck },
        { to: "/stock", label: "Остатки", icon: Boxes },
        ...(canAudit ? [{ to: "/audit", label: "Журнал", icon: ScrollText }] : []),
      ],
    ],
  ];

  const [surname, ...givenNames] = me.full_name.split(" ");

  // Свёрнутое меню прячет подписи только на широком экране: выезжающее всегда с подписями.
  const hideLabel = collapsed ? "lg:sr-only" : "";

  return (
    <div className="flex h-dvh flex-col lg:flex-row">
      <header className="flex items-center gap-3 px-4 py-2.5 lg:hidden">
        <button
          type="button"
          aria-label="Меню"
          aria-expanded={open}
          className="grid size-9 place-items-center rounded-lg border border-line bg-card"
          onClick={() => setOpen(true)}
        >
          <Menu className="size-4" aria-hidden="true" />
        </button>
        <Logo className="size-7" />
        <img src={wordmark} alt="Мастерская" className="h-5.5" />
      </header>
      {open && <div className="fixed inset-0 z-10 bg-ink/30 lg:hidden" onClick={() => setOpen(false)} />}
      <aside
        className={`${open ? "fixed inset-y-0 left-0 z-20 flex bg-paper shadow-xl" : "hidden lg:flex"} w-60 shrink-0 flex-col px-3 py-4 lg:static lg:shadow-none ${collapsed ? "lg:w-16" : ""}`}
      >
        <div className={`mb-6 flex items-center gap-2.5 px-1 ${collapsed ? "lg:flex-col" : ""}`}>
          <Logo className="size-8 shrink-0" />
          <span className={`flex-1 ${hideLabel}`}>
            <img src={wordmark} alt="Мастерская" className="h-5.5" />
          </span>
          <button
            type="button"
            aria-label={collapsed ? "Развернуть меню" : "Свернуть меню"}
            className="hidden size-8 place-items-center rounded-lg text-muted hover:bg-line/60 hover:text-ink lg:grid"
            onClick={toggleCollapsed}
          >
            <ChevronsLeft className={`size-4 ${collapsed ? "rotate-180" : ""}`} aria-hidden="true" />
          </button>
        </div>
        <nav className="-mx-3 flex-1 space-y-6 overflow-y-auto px-3">
          {groups.map(([group, items]) => (
            <div key={group}>
              <div className={`mb-1.5 px-2.5 text-xs text-muted ${hideLabel}`}>{group}</div>
              <ul className="space-y-0.5">
                {items.map(({ to, label, icon: Icon, count }) => (
                  <li key={to}>
                    <NavLink
                      to={to}
                      aria-label={label}
                      title={collapsed ? label : undefined}
                      onClick={() => setOpen(false)}
                      className={({ isActive }) =>
                        `group relative flex h-9 items-center gap-2.5 rounded-lg px-2.5 transition-colors ${collapsed ? "lg:justify-center lg:px-0" : ""} ${isActive ? "bg-card font-medium shadow-raised ring-1 ring-line" : "text-ink/75 hover:bg-line/60 hover:text-ink"}`
                      }
                    >
                      <span className="absolute inset-y-2 -left-3 w-[3px] rounded-r bg-flame opacity-0 transition-opacity group-aria-[current=page]:opacity-100" />
                      <Icon className="size-4 shrink-0" aria-hidden="true" />
                      <span className={`flex-1 ${hideLabel}`}>{label}</span>
                      {!!count && (
                        <span
                          className={`rounded-md bg-line/70 px-1.5 text-xs font-medium text-muted tabular-nums ${hideLabel}`}
                        >
                          {count}
                        </span>
                      )}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div
          className={`flex items-center gap-2.5 border-t border-line pt-4 ${collapsed ? "lg:flex-col" : ""}`}
        >
          <Avatar name={me.full_name} />
          <div className={`min-w-0 flex-1 leading-tight ${hideLabel}`}>
            {/* Фамилия на своей строке: ФИО целиком в ширину меню не помещается. */}
            <div className="text-sm font-medium break-words">
              <span className="block">{surname}</span> {givenNames.join(" ")}
            </div>
            <div className="mt-0.5 text-xs text-muted">{me.role_label}</div>
          </div>
          <button
            type="button"
            aria-label="Выйти"
            title="Выйти"
            className="grid size-8 shrink-0 place-items-center rounded-lg text-muted hover:bg-line/60 hover:text-ink"
            onClick={() => void logout()}
          >
            <LogOut className="size-4" aria-hidden="true" />
          </button>
        </div>
      </aside>
      <main className="relative min-h-0 min-w-0 flex-1 overflow-auto border-t border-line bg-card lg:my-2 lg:mr-2 lg:rounded-2xl lg:border">
        <Outlet />
      </main>
    </div>
  );
}

/** Обычная страница с полями. Чат живёт без них: он занимает всю высоту. */
export function PaddedPage() {
  return (
    <div className="p-4 lg:p-6">
      <Outlet />
    </div>
  );
}
