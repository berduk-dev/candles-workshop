import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";

import { Button, Card, ErrorBox, Field, inputClass, PageHeader } from "../components/ui";
import { api } from "../lib/api";
import { todayIso } from "../lib/format";
import type { MaterialStock } from "../lib/types";

interface Line {
  material_code: string;
  qty: string;
  price: string;
  currency: string;
  comment: string;
}

const emptyLine: Line = { material_code: "", qty: "", price: "", currency: "RUB", comment: "" };
const decimal = (value: string) => value.trim().replace(",", ".");

export function ReceiptPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [supplier, setSupplier] = useState("");
  const [date, setDate] = useState(todayIso());
  const [lines, setLines] = useState<Line[]>([emptyLine]);
  const { data: materials } = useQuery({
    queryKey: ["materials"],
    queryFn: () => api.get<MaterialStock[]>("/materials"),
  });
  const { data: suppliers } = useQuery({
    queryKey: ["suppliers"],
    queryFn: () => api.get<{ id: number; name: string }[]>("/suppliers"),
  });
  const save = useMutation({
    mutationFn: () =>
      api.post("/receipts", {
        supplier_name: supplier,
        received_at: date,
        lines: lines.map((l) => ({
          ...l,
          qty: decimal(l.qty),
          price: decimal(l.price),
          comment: l.comment || null,
        })),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries();
      await navigate("/batches");
    },
  });

  const setLine = (index: number, changes: Partial<Line>) =>
    setLines((all) => all.map((l, i) => (i === index ? { ...l, ...changes } : l)));

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate();
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <PageHeader title="Новое поступление" parent={{ to: "/batches", label: "Партии" }} />
      <Card title="Документ">
        <div className="grid max-w-2xl gap-3 sm:grid-cols-2">
          <Field label="Поставщик">
            <input
              className={inputClass}
              list="suppliers"
              value={supplier}
              onChange={(e) => setSupplier(e.target.value)}
              required
            />
            <datalist id="suppliers">
              {suppliers?.map((s) => (
                <option key={s.id} value={s.name} />
              ))}
            </datalist>
          </Field>
          <Field label="Дата поступления">
            <input
              className={inputClass}
              type="date"
              max={todayIso()}
              value={date}
              onChange={(e) => setDate(e.target.value)}
              required
            />
          </Field>
        </div>
      </Card>
      <Card title="Строки">
        <p className="-mt-3 mb-4 text-muted">Каждая строка станет партией «На проверке».</p>
        <div className="space-y-2">
          {lines.map((line, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr_6rem_2fr_auto]">
              <select
                aria-label="Материал"
                className={inputClass}
                value={line.material_code}
                onChange={(e) => setLine(i, { material_code: e.target.value })}
                required
              >
                <option value="">Материал…</option>
                {materials?.map((m) => (
                  <option key={m.id} value={m.code}>
                    {m.name}, {m.unit}
                  </option>
                ))}
              </select>
              <input
                className={inputClass}
                placeholder="Количество"
                inputMode="decimal"
                value={line.qty}
                onChange={(e) => setLine(i, { qty: e.target.value })}
                required
              />
              <input
                className={inputClass}
                placeholder="Цена"
                inputMode="decimal"
                value={line.price}
                onChange={(e) => setLine(i, { price: e.target.value })}
                required
              />
              <select
                aria-label="Валюта"
                className={inputClass}
                value={line.currency}
                onChange={(e) => setLine(i, { currency: e.target.value })}
              >
                {["RUB", "USD", "EUR"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <input
                className={inputClass}
                placeholder="Комментарий"
                value={line.comment}
                onChange={(e) => setLine(i, { comment: e.target.value })}
              />
              <Button
                variant="ghost"
                icon={Trash2}
                aria-label="Убрать строку"
                title="Убрать строку"
                className="w-9 px-0! hover:text-bad"
                disabled={lines.length === 1}
                onClick={() => setLines((all) => all.filter((_, k) => k !== i))}
              />
            </div>
          ))}
        </div>
        <Button
          variant="secondary"
          icon={Plus}
          className="mt-3"
          onClick={() => setLines((all) => [...all, emptyLine])}
        >
          Добавить строку
        </Button>
      </Card>
      <ErrorBox error={save.error} />
      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending}>
          Оформить поступление
        </Button>
      </div>
    </form>
  );
}
